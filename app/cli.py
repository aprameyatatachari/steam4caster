"""Operational command line: ``python -m app.cli --help``."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer

from app.core.config import get_settings
from app.core.container import Container, RecordingDispatcher, build_container
from app.core.logging import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Steam4Caster operations.")
T = TypeVar("T")


def _run[T](job: Callable[[Container], Awaitable[T]]) -> T:
    settings = get_settings()
    # Logs go to stderr so stdout stays machine-readable JSON.
    configure_logging(settings.log_level, json_logs=False, stream=sys.stderr)

    async def main() -> T:
        container = build_container(settings, pooled=False)
        container.tasks = RecordingDispatcher()  # CLI work runs inline, never enqueued
        try:
            return await job(container)
        finally:
            await container.aclose()

    return asyncio.run(main())


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


async def _resolve_game(
    container: Container, session: Any, game_id: str | None, app_id: int | None
) -> Any:
    from app.services.catalog import CatalogService

    catalog = CatalogService(session, container)
    if game_id:
        return await catalog.get_with_info(uuid.UUID(game_id))
    if app_id:
        return await catalog.lookup(steam_app_id=app_id)
    raise typer.BadParameter("Provide --game-id or --steam-app-id.")


@app.command()
def migrate(revision: str = "head") -> None:
    """Apply database migrations."""
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config("alembic.ini"), revision)


@app.command("init-env")
def init_env(target: Path = Path(".env"), example: Path = Path(".env.example")) -> None:
    """Create .env from .env.example with freshly generated secrets (never overwrites)."""
    import secrets

    if target.exists():
        print(f"{target} already exists; left unchanged")
        return
    generated = {
        "JWT_SECRET_KEY": secrets.token_urlsafe(64),
        "SECRETS_ENCRYPTION_KEY": secrets.token_urlsafe(48),
    }
    lines = []
    for line in example.read_text(encoding="utf-8").splitlines():
        name = line.split("=", 1)[0]
        lines.append(f"{name}={generated[name]}" if name in generated else line)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"created {target} with generated secrets")


@app.command("init-push")
def init_push(target: Path = Path(".env")) -> None:
    """Enable browser push: generate VAPID keys into .env if they are not set yet."""
    from app.core.security import generate_vapid_keypair

    if not target.exists():
        raise typer.BadParameter(f"{target} does not exist; run init-env first.")
    lines = target.read_text(encoding="utf-8").splitlines()
    values = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in lines if "=" in line}
    if values.get("VAPID_PUBLIC_KEY") and values.get("VAPID_PRIVATE_KEY"):
        print("browser push is already configured; left unchanged")
        return
    public, private = generate_vapid_keypair()
    wanted = {
        "VAPID_PUBLIC_KEY": public,
        "VAPID_PRIVATE_KEY": private,
        "VAPID_SUBJECT": values.get("VAPID_SUBJECT") or "mailto:admin@example.com",
    }
    seen: set[str] = set()
    out = []
    for line in lines:
        name = line.split("=", 1)[0]
        if name in wanted:
            out.append(f"{name}={wanted[name]}")
            seen.add(name)
        else:
            out.append(line)
    out.extend(f"{name}={value}" for name, value in wanted.items() if name not in seen)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    # The private key is written to the file only; it is never printed.
    print(f"browser push enabled: VAPID keys written to {target}")


@app.command("generate-vapid-keys")
def generate_vapid_keys() -> None:
    """Print a new VAPID key pair for Web Push. Store the private key as a secret."""
    from app.core.security import generate_vapid_keypair

    public, private = generate_vapid_keypair()
    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")
    print("VAPID_SUBJECT=mailto:you@example.com")


@app.command("export-openapi")
def export_openapi(out: Path = Path("docs/openapi.json")) -> None:
    """Write the generated OpenAPI document to a file."""
    from fastapi.openapi.utils import get_openapi

    from app.main import create_app

    application = create_app(get_settings())
    schema = get_openapi(
        title=application.title, version=application.version,
        description=application.description, routes=application.routes,
    )  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(f"wrote {out}")


@app.command("sync-shops")
def sync_shops() -> None:
    """Fetch the provider's shop list and resolve the Steam shop."""

    async def job(container: Container) -> dict[str, Any]:
        from app.services.catalog import CatalogService

        async with container.db.session() as session:
            catalog = CatalogService(session, container)
            count = await catalog.sync_shops()
            steam = await catalog.steam_shop()
            return {"shops": count, "steam_provider_shop_id": steam.provider_shop_id}

    _print(_run(job))


@app.command()
def backfill(
    country: Annotated[str, typer.Option(help="ISO country code, e.g. IN.")],
    game_id: Annotated[str | None, typer.Option()] = None,
    steam_app_id: Annotated[int | None, typer.Option()] = None,
    watched: Annotated[bool, typer.Option(help="Backfill every watched game.")] = False,
    full: Annotated[bool, typer.Option(help="Re-read the entire history.")] = False,
) -> None:
    """Backfill price history. Restartable: re-running never duplicates observations."""

    async def job(container: Container) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from app.models import Game
        from app.repositories.forecasts import watched_series
        from app.services.catalog import CatalogService
        from app.services.prices import PriceService

        results = []
        async with container.db.session() as session:
            shop = await CatalogService(session, container).steam_shop()
            if watched:
                ids = {g for g, c in await watched_series(session) if c == country.upper()}
                games = list(await session.scalars(select(Game).where(Game.id.in_(ids))))
            else:
                games = [await _resolve_game(container, session, game_id, steam_app_id)]
            for game in games:
                result = await PriceService(session, container).ingest_history(
                    game, shop, country.upper(), full=full
                )
                results.append({"game": game.title, **result.__dict__})
        return results

    _print(_run(job))


@app.command("build-dataset")
def build_dataset(
    out: Path = Path("artifacts/dataset.csv"),
    country: Annotated[str | None, typer.Option()] = None,
    step_days: int = 14,
) -> None:
    """Build the leakage-safe feature dataset from stored history."""

    async def job(container: Container) -> dict[str, Any]:
        from app.services.catalog import CatalogService
        from app.services.models import ModelService

        async with container.db.session() as session:
            shop = await CatalogService(session, container).steam_shop()
            frame = await ModelService(session, container).build_dataset(
                shop, country=country.upper() if country else None, step_days=step_days
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(out, index=False)
        return {"rows": len(frame), "path": str(out)}

    _print(_run(job))


@app.command("evaluate-baseline")
def evaluate_baseline() -> None:
    """Score the deterministic baseline on all labelled history."""

    async def job(container: Container) -> dict[str, Any]:
        from app.services.catalog import CatalogService
        from app.services.models import ModelService

        async with container.db.session() as session:
            shop = await CatalogService(session, container).steam_shop()
            return await ModelService(session, container).evaluate_baseline(shop)

    _print(_run(job))


@app.command()
def train() -> None:
    """Train and evaluate a CANDIDATE model. Does not activate it."""

    async def job(container: Container) -> dict[str, Any]:
        from app.forecasting.training.trainer import InsufficientTrainingData
        from app.services.catalog import CatalogService
        from app.services.models import ModelService

        async with container.db.session() as session:
            shop = await CatalogService(session, container).steam_shop()
            try:
                row = await ModelService(session, container).train(shop)
            except InsufficientTrainingData as exc:
                return {"trained": False, "reason": str(exc)}
            return {
                "trained": True,
                "version": row.version,
                "artifact": row.artifact_uri,
                "gates": row.metrics.get("gates"),
                "sale_30d": row.metrics["sale"]["30"],
            }

    _print(_run(job))


@app.command("list-models")
def list_models() -> None:
    """List registered model versions."""

    async def job(container: Container) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from app.models import ModelVersion

        async with container.db.session() as session:
            rows = await session.scalars(select(ModelVersion).order_by(ModelVersion.created_at))
            return [
                {
                    "version": r.version, "status": r.status, "created_at": r.created_at,
                    "gates_passed": (r.metrics.get("gates") or {}).get("passed"),
                }
                for r in rows
            ]  # fmt: skip

    _print(_run(job))


@app.command()
def activate(
    version: str,
    force: Annotated[bool, typer.Option(help="Activate even if promotion gates failed.")] = False,
) -> None:
    """Promote a candidate to ACTIVE (refused unless promotion gates passed)."""

    async def job(container: Container) -> dict[str, Any]:
        from app.core.errors import AppError
        from app.services.models import ModelService

        async with container.db.session() as session:
            try:
                row = await ModelService(session, container).activate(version, force=force)
            except AppError as exc:
                return {"activated": False, "reason": exc.message, "details": exc.details}
            return {"activated": True, "version": row.version}

    _print(_run(job))


@app.command()
def rollback() -> None:
    """Retire the active model and restore the previous one (or the baseline)."""

    async def job(container: Container) -> dict[str, Any]:
        from app.services.models import ModelService

        async with container.db.session() as session:
            previous = await ModelService(session, container).rollback()
            return {"active": previous.version if previous else "baseline"}

    _print(_run(job))


@app.command()
def forecast(
    country: Annotated[str, typer.Option()],
    game_id: Annotated[str | None, typer.Option()] = None,
    steam_app_id: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Generate a forecast and recommendation for one game and region."""

    async def job(container: Container) -> dict[str, Any]:
        from app.schemas.catalog import forecast_out, recommendation_out
        from app.services.catalog import CatalogService
        from app.services.forecasts import ForecastService

        async with container.db.session() as session:
            game = await _resolve_game(container, session, game_id, steam_app_id)
            shop = await CatalogService(session, container).steam_shop()
            service = ForecastService(session, container)
            row = await service.get_or_generate(game, shop, country.upper())
            rec = await service.recommend(row)
            return {
                "game": game.title,
                "forecast": forecast_out(row, shop.slug).model_dump(mode="json"),
                "recommendation": recommendation_out(rec).model_dump(mode="json"),
            }

    _print(_run(job))


@app.command("evaluate-outcomes")
def evaluate_outcomes() -> None:
    """Fill in outcomes for forecasts whose horizons have expired."""
    from app.tasks import jobs

    _print(_run(jobs.evaluate_outcomes))


@app.command("dispatch-outbox")
def dispatch_outbox() -> None:
    """Deliver pending notifications once."""
    from app.tasks import jobs

    _print(_run(jobs.dispatch_outbox))


@app.command("seed-demo")
def seed_demo(
    country: Annotated[str, typer.Option()] = "IN",
    email: Annotated[str, typer.Option()] = "demo@example.com",
) -> None:
    """Load the synthetic demo catalogue (requires PRICE_PROVIDER=fake)."""
    settings = get_settings()
    if settings.price_provider != "fake":
        raise typer.BadParameter("seed-demo only runs with PRICE_PROVIDER=fake.")

    async def job(container: Container) -> dict[str, Any]:
        import secrets

        from sqlalchemy import select

        from app.models import User
        from app.providers.pricing.fake import FakePriceProvider
        from app.services.alerts import AlertService
        from app.services.auth import AuthService
        from app.services.catalog import CatalogService
        from app.services.forecasts import ForecastService
        from app.services.watchlist import WatchlistService

        region = country.upper()
        async with container.db.session() as session:
            catalog = CatalogService(session, container)
            shop = await catalog.steam_shop()
            user = (await session.scalars(select(User).where(User.email == email))).first()
            created_password = None
            if user is None:
                created_password = secrets.token_urlsafe(12)
                user, _ = await AuthService(session, container).register(
                    email=email, password=created_password, country=region, currency=None,
                    timezone="UTC",
                )  # fmt: skip
            games = []
            for provider_id in FakePriceProvider.catalog_ids():
                info = await container.price_provider.get_game_info(provider_id)
                assert info is not None
                games.append(await catalog.lookup(steam_app_id=info.steam_app_id))
            existing = {
                v.game.id for v in await WatchlistService(session, container).list_entries(user)
            }
            forecasts = 0
            for game in games:
                if game.id not in existing:
                    await WatchlistService(session, container).create(
                        user, game_id=game.id, country=region, currency=None,
                        target_price_minor=None, min_discount_pct=50, max_wait_days=60,
                        historical_low_only=False, notify_on_buy=True, channels=None,
                    )  # fmt: skip
                await ForecastService(session, container).get_or_generate(game, shop, region)
                await AlertService(session, container).evaluate_series(game, shop, region)
                forecasts += 1
            result: dict[str, Any] = {"user": email, "games": len(games), "forecasts": forecasts}
            if created_password:
                # Shown once so the demo account can be used; it is not stored anywhere else.
                result["generated_password"] = created_password
            return result

    _print(_run(job))


if __name__ == "__main__":
    app()
