from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from app.application.schema_validator import validate_quote_schema
from app.infrastructure.settings import get_settings

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_HEALTH_WAIT_SECONDS = 30.0

console = Console()


@dataclass
class HealthCheckResult:
    ready: bool
    url: str
    message: str
    status_code: int | None = None


@dataclass
class StreamRunState:
    model_name: str
    total_items: int
    start_time: float
    completed_items: int = 0
    active_tool: str = "Waiting for events"
    last_item: str = "None yet"
    current_item_key: str | None = None
    current_item_name: str | None = None
    estimation_id: str | None = None
    final_status: str = "streaming"
    quote_received: bool = False
    errors: list[str] = field(default_factory=list)


def format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02}:{minutes:02}:{remaining_seconds:02}"
    return f"{minutes:02}:{remaining_seconds:02}"


def build_status_text(state: StreamRunState, now: float | None = None) -> str:
    if now is None:
        now = time.monotonic()

    lines = [
        f"Elapsed: {format_elapsed(now - state.start_time)}",
        f"Model: {state.model_name}",
        f"Current Activity: {state.active_tool}",
        f"Progress: {state.completed_items}/{state.total_items}",
        f"Last Item: {state.last_item}",
        f"Current Item Key: {state.current_item_key or 'Waiting'}",
        f"Final Status: {state.final_status}",
        f"Quote Received: {'yes' if state.quote_received else 'no'}",
        f"Errors: {len(state.errors)}",
    ]
    if state.errors:
        lines.append(f"Latest Error: {state.errors[-1]}")
    return "\n".join(lines)


def apply_stream_event(state: StreamRunState, event_payload: dict[str, Any]) -> None:
    event_type = str(event_payload.get("event", ""))
    raw_data = event_payload.get("data", {})
    data = raw_data if isinstance(raw_data, dict) else {}

    state.estimation_id = str(
        event_payload.get("estimation_id")
        or data.get("estimation_id")
        or state.estimation_id
        or ""
    ) or None

    if event_type == "estimation_started":
        state.active_tool = "Initializing estimation"
        return

    if event_type == "item_started":
        item_name = str(data.get("item_name", "Unknown Item"))
        state.current_item_name = item_name
        state.current_item_key = str(data.get("item_key", "")) or None
        state.last_item = item_name
        state.active_tool = f"Starting item: {item_name}"
        return

    if event_type == "llm_waiting":
        state.active_tool = str(data.get("message", "Waiting for LLM response"))
        return

    if event_type == "tool_started":
        tool_name = str(data.get("tool", "Unknown tool"))
        state.active_tool = f"Running tool: {tool_name}"
        return

    if event_type == "tool_waiting":
        tool_name = str(data.get("tool", "Unknown tool"))
        elapsed = data.get("elapsed_seconds", "?")
        state.active_tool = f"{tool_name} still running ({elapsed}s)"
        return

    if event_type == "tool_finished":
        tool_name = str(data.get("tool", "Unknown tool"))
        if data.get("status") == "error":
            message = str(data.get("message", f"{tool_name} failed"))
            state.errors.append(message)
            state.active_tool = f"{tool_name} failed"
        else:
            state.active_tool = f"{tool_name} finished"
        return

    if event_type == "validation_retry":
        attempt = data.get("attempt", "?")
        state.active_tool = f"Retrying item validation (attempt {attempt})"
        return

    if event_type == "item_complete":
        state.completed_items += 1
        item_name = str(data.get("item_name", state.current_item_name or "Unknown Item"))
        state.last_item = item_name
        state.current_item_name = item_name
        state.current_item_key = str(data.get("item_key", state.current_item_key or "")) or None
        state.active_tool = f"Completed item: {item_name}"
        return

    if event_type == "quote_complete":
        state.quote_received = True
        state.active_tool = "Finalizing quote"
        return

    if event_type == "estimation_complete":
        state.final_status = str(data.get("status", "completed"))
        state.active_tool = f"Completed ({state.final_status})"
        return

    if event_type == "error":
        message = str(data.get("message", "Unknown error"))
        state.errors.append(message)
        state.active_tool = "Run failed"


class StreamDashboard:
    def __init__(self, state: StreamRunState, progress: Progress) -> None:
        self._state = state
        self._progress = progress

    def __rich__(self) -> Group:
        renderables = [
            Panel(
                build_status_text(self._state),
                title="[bold blue]Estimation Status[/bold blue]",
                expand=False,
            ),
            self._progress,
        ]
        if self._state.errors:
            renderables.append(
                Panel(
                    "\n".join(f"- {error}" for error in self._state.errors[-3:]),
                    title="[bold red]Recent Errors[/bold red]",
                    expand=False,
                )
            )
        return Group(*renderables)


def get_configured_model() -> str:
    try:
        return get_settings().openai_model
    except Exception:
        return "gpt-4o-mini"


async def probe_docker_health(
    base_url: str,
    timeout_seconds: float = 2.0,
) -> HealthCheckResult:
    health_url = f"{base_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(health_url)
        if response.status_code == 200:
            return HealthCheckResult(
                ready=True,
                url=health_url,
                message="Docker API is healthy.",
                status_code=response.status_code,
            )
        return HealthCheckResult(
            ready=False,
            url=health_url,
            message=f"Docker API health check failed with status {response.status_code}.",
            status_code=response.status_code,
        )
    except httpx.HTTPError as exc:
        return HealthCheckResult(
            ready=False,
            url=health_url,
            message=f"Docker API health check failed: {exc}",
        )


async def wait_for_docker_health(
    base_url: str,
    timeout_seconds: float = DEFAULT_HEALTH_WAIT_SECONDS,
    poll_interval: float = 1.0,
) -> HealthCheckResult:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    last_result = await probe_docker_health(base_url)
    if last_result.ready or timeout_seconds <= 0:
        return last_result

    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        last_result = await probe_docker_health(base_url)
        if last_result.ready:
            return last_result
    return last_result


def load_payload(file_path: str) -> dict[str, Any]:
    with open(file_path, encoding="utf-8") as file_handle:
        return json.load(file_handle)


def count_total_items(payload: dict[str, Any]) -> int:
    categories = payload.get("categories", {})
    if not isinstance(categories, dict):
        return 0
    return sum(len(items) for items in categories.values() if isinstance(items, list))


def build_run_summary(
    *,
    health_check: HealthCheckResult,
    state: StreamRunState,
    completed_items: list[dict[str, Any]],
    final_quote: dict[str, Any] | None,
    final_status: str,
) -> dict[str, Any]:
    schema_valid = False
    schema_error: str | None = None
    if final_quote is not None:
        try:
            validate_quote_schema(final_quote)
            schema_valid = True
        except ValueError as exc:
            schema_error = str(exc)

    missing_ingredients = 0
    for item in completed_items:
        ingredients = item.get("ingredients", [])
        if isinstance(ingredients, list):
            missing_ingredients += sum(
                1
                for ingredient in ingredients
                if isinstance(ingredient, dict)
                and ingredient.get("source") == "not_available"
            )

    return {
        "api_reachable": health_check.ready,
        "health_message": health_check.message,
        "health_status_code": health_check.status_code,
        "completed_items": len(completed_items),
        "total_items": state.total_items,
        "final_status": final_status,
        "quote_emitted": final_quote is not None,
        "schema_valid": schema_valid,
        "schema_error": schema_error,
        "missing_ingredients": missing_ingredients,
        "runtime_errors": list(state.errors),
    }


def print_summary_table(items: list[dict[str, Any]], guest_count: int) -> None:
    table = Table(title="Final Catering Estimation Summary", show_lines=True)
    table.add_column("Category", style="cyan")
    table.add_column("Item Name", style="white", no_wrap=False)
    table.add_column("Est. Cost/Serving", style="green", justify="right")
    table.add_column("Missing Ingredients", style="red")

    grand_total_per_serving = 0.0

    for item in items:
        cost = item.get("ingredient_cost_per_unit")
        if isinstance(cost, (int, float)):
            cost_str = f"${float(cost):.2f}"
            grand_total_per_serving += float(cost)
        else:
            cost_str = "[yellow]Manual Quote Required[/yellow]"

        ingredients = item.get("ingredients", [])
        missing_count = 0
        if isinstance(ingredients, list):
            missing_count = sum(
                1
                for ingredient in ingredients
                if isinstance(ingredient, dict)
                and ingredient.get("source") == "not_available"
            )
        missing_str = str(missing_count) if missing_count > 0 else "[dim]-[/dim]"

        table.add_row(
            str(item.get("category", "Unknown")),
            str(item.get("item_name", "Unknown")),
            cost_str,
            missing_str,
        )

    console.print()
    console.print(table)

    total_event_cost = grand_total_per_serving * guest_count
    summary_panel = Panel(
        f"[bold]Cost Per Guest (Ingredients Base):[/bold] ${grand_total_per_serving:.2f}\n"
        f"[bold]Total Event Ingredient Cost ({guest_count} guests):[/bold] ${total_event_cost:.2f}\n"
        "[dim italic]* Note: This is raw ingredient cost only, excluding markup, labor, and rentals.[/dim italic]",
        title="[bold green]Final Projections[/bold green]",
        expand=False,
    )
    console.print(summary_panel)


def print_run_summary(summary: dict[str, Any]) -> None:
    lines = [
        f"Docker health: {'ok' if summary['api_reachable'] else 'failed'}",
        f"Completed items: {summary['completed_items']}/{summary['total_items']}",
        f"Final status: {summary['final_status']}",
        f"Quote emitted: {'yes' if summary['quote_emitted'] else 'no'}",
        f"Schema valid: {'yes' if summary['schema_valid'] else 'no'}",
        f"Missing ingredients flagged: {summary['missing_ingredients']}",
    ]
    if summary["schema_error"]:
        lines.append(f"Schema error: {summary['schema_error']}")
    if summary["runtime_errors"]:
        lines.append("Recent runtime errors:")
        lines.extend(f"- {error}" for error in summary["runtime_errors"][-3:])

    console.print()
    console.print(
        Panel(
            "\n".join(lines),
            title="[bold magenta]Challenge Run Summary[/bold magenta]",
            expand=False,
        )
    )


async def stream_estimation(
    file_path: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    health_wait_seconds: float = DEFAULT_HEALTH_WAIT_SECONDS,
) -> int:
    try:
        payload = load_payload(file_path)
    except Exception as exc:
        console.print(f"[bold red]Failed to load {file_path}:[/bold red] {exc}")
        return 1

    total_items = count_total_items(payload)
    if total_items == 0:
        console.print("[bold yellow]Warning: Payload contains 0 items.[/bold yellow]")
        return 1

    health_check = await wait_for_docker_health(
        base_url,
        timeout_seconds=health_wait_seconds,
    )
    if not health_check.ready:
        console.print(f"[bold red]{health_check.message}[/bold red]")
        return 1

    console.print(f"[dim]Health check passed: {health_check.url}[/dim]")

    completed_items: list[dict[str, Any]] = []
    final_quote: dict[str, Any] | None = None
    final_status = "streaming"

    state = StreamRunState(
        model_name=get_configured_model(),
        total_items=total_items,
        start_time=time.monotonic(),
        active_tool="Connecting to Docker API",
    )

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )
    main_task = progress.add_task("[cyan]Processing Menu Items...", total=total_items)
    timeout = httpx.Timeout(5.0, read=None)

    with Live(StreamDashboard(state, progress), console=console, refresh_per_second=4):
        try:
            async with (
                httpx.AsyncClient(timeout=timeout) as client,
                client.stream("POST", f"{base_url.rstrip('/')}/estimate", json=payload) as response,
            ):
                if response.status_code != 200:
                    response_text = await response.aread()
                    error_message = (
                        f"API Error {response.status_code}: "
                        f"{response_text.decode(errors='replace')}"
                    )
                    state.errors.append(error_message)
                    state.active_tool = "Request failed"
                else:
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data: "):
                            continue

                        data_str = line.replace("data: ", "", 1)
                        if data_str == "{}":
                            continue

                        try:
                            event_payload = json.loads(data_str)
                        except json.JSONDecodeError as exc:
                            state.errors.append(f"Malformed SSE payload: {exc}")
                            continue

                        apply_stream_event(state, event_payload)
                        event_type = str(event_payload.get("event", ""))
                        raw_data = event_payload.get("data", {})
                        data = raw_data if isinstance(raw_data, dict) else {}

                        if event_type == "item_complete":
                            item_name = str(data.get("item_name", "Unknown Item"))
                            console.print(f"[dim green]✓ Item Assessed:[/dim green] {item_name}")
                            completed_items.append(data)
                            progress.update(main_task, completed=min(state.completed_items, total_items))
                        elif event_type == "quote_complete":
                            final_quote = data
                        elif event_type == "estimation_complete":
                            final_status = str(data.get("status", "completed"))
                            progress.update(main_task, completed=min(state.completed_items, total_items))
                            console.print("[bold green]Orchestration Complete![/bold green]")
                            break
        except httpx.ConnectError:
            state.errors.append(
                "Connection refused. Is the Docker cluster running via 'docker compose up'?"
            )
            state.active_tool = "Connection failed"

    print_summary_table(completed_items, int(payload.get("guest_count_estimate", 1)))
    summary = build_run_summary(
        health_check=health_check,
        state=state,
        completed_items=completed_items,
        final_quote=final_quote,
        final_status=final_status if final_status != "streaming" else state.final_status,
    )
    print_run_summary(summary)

    if (
        not summary["api_reachable"]
        or not summary["quote_emitted"]
        or not summary["schema_valid"]
        or summary["runtime_errors"]
    ):
        return 1
    return 0


async def main_async(args: argparse.Namespace) -> int:
    return await stream_estimation(
        args.file,
        base_url=args.base_url,
        health_wait_seconds=args.health_wait_seconds,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Live TUI viewer for the Yes Chef SSE estimation API"
    )
    parser.add_argument(
        "--file",
        type=str,
        default="data/menu_spec.json",
        help="Path to the JSON menu spec file",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=DEFAULT_BASE_URL,
        help="Docker-exposed API base URL",
    )
    parser.add_argument(
        "--health-wait-seconds",
        type=float,
        default=DEFAULT_HEALTH_WAIT_SECONDS,
        help="How long to wait for Docker health before giving up",
    )
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main_async(build_parser().parse_args())))
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Stream interrupted by user.[/bold yellow]")
