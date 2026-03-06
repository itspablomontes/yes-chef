import argparse
import asyncio
import json

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

console = Console()

async def stream_estimation(file_path: str):
    # Load the JSON payload
    try:
        with open(file_path) as f:
            payload = json.load(f)
    except Exception as e:
        console.print(f"[bold red]Failed to load {file_path}:[/bold red] {e}")
        return

    # Count total items to configure progress bar
    total_items = sum(len(items) for items in payload.get("categories", {}).values())
    if total_items == 0:
        console.print("[bold yellow]Warning: Payload contains 0 items.[/bold yellow]")
        return
        
    console.print(
        Panel(
            f"[bold blue]Testing Yes Chef Estimation API[/bold blue]\n"
            f"[dim]Event: {payload.get('event', 'Unknown')} | Items: {total_items}[/dim]"
        )
    )

    completed_items = []
    
    # Configure the Progress Bar
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=False,
    ) as progress:
        
        main_task = progress.add_task("[cyan]Processing Menu Items...", total=total_items)
        
        timeout = httpx.Timeout(5.0, read=None) # SSE streams can stay open arbitrarily long
        
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", "http://localhost:8000/estimate", json=payload) as response:
                    # Raise early if connection refused or 422
                    if response.status_code != 200:
                        await response.aread()
                        console.print(f"[bold red]API Error {response.status_code}:[/bold red] {response.text}")
                        return
                    
                    # Read the SSE lines
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        
                        if line.startswith("data: "):
                            data_str = line.replace("data: ", "", 1)
                            
                            # Skip empty heartbeats
                            if data_str == "{}":
                                continue
                                
                            try:
                                event_payload = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                                
                            event_type = event_payload.get("event")
                            data = event_payload.get("data", {})
                            
                            if event_type == "item_complete":
                                item_name = data.get("item_name", "Unknown Item")
                                progress.console.print(f"[dim green]✓ Item Assessed:[/dim green] {item_name}")
                                completed_items.append(data)
                                progress.update(main_task, advance=1)
                                
                            elif event_type == "estimation_complete":
                                progress.update(main_task, completed=total_items)
                                progress.console.print("[bold green]Orchestration Complete![/bold green]")
                                break
                                
                            elif event_type == "error":
                                console.print(f"[bold red]Runtime Error:[/bold red] {data.get('message')}")
                                return
        except httpx.ConnectError:
            console.print(
                "[bold red]Connection Refused:[/bold red] Is the Docker cluster running via 'docker compose up'?"
            )
            return

    # Once stream is complete, display the summary table
    print_summary_table(completed_items, payload.get('guest_count_estimate', 1))

def print_summary_table(items: list[dict], guest_count: int):
    table = Table(title="Final Catering Estimation Summary", show_lines=True)
    table.add_column("Category", style="cyan")
    table.add_column("Item Name", style="white", no_wrap=False)
    table.add_column("Est. Cost/Serving", style="green", justify="right")
    table.add_column("Missing Ingredients", style="red")

    grand_total_per_serving = 0.0

    for item in items:
        cost = item.get("ingredient_cost_per_unit")
        
        # Format Cost
        if cost is None:
            cost_str = "[yellow]Manual Quote Required[/yellow]"
        else:
            cost_str = f"${cost:.2f}"
            grand_total_per_serving += cost

        # Count missing (not_available) ingredients
        ingredients = item.get("ingredients", [])
        missing_count = sum(1 for i in ingredients if i.get("source") == "not_available")
        missing_str = str(missing_count) if missing_count > 0 else "[dim]-[/dim]"

        table.add_row(
            item.get("category", "Unknown"),
            item.get("item_name", "Unknown"),
            cost_str,
            missing_str
        )

    console.print()
    console.print(table)
    
    total_event_cost = grand_total_per_serving * guest_count
    
    summary_panel = Panel(
        f"[bold]Cost Per Guest (Ingredients Base):[/bold] ${grand_total_per_serving:.2f}\n"
        f"[bold]Total Event Ingredient Cost ({guest_count} guests):[/bold] ${total_event_cost:.2f}\n"
        f"[dim italic]* Note: This is raw ingredient cost only, excluding markup, labor, and rentals.[/dim italic]",
        title="[bold green]Final Projections[/bold green]",
        expand=False
    )
    console.print(summary_panel)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the Yes Chef SSE Streaming Estimation API")
    parser.add_argument("--file", type=str, default="data/menu_spec.json", help="Path to the JSON menu spec file")
    
    args = parser.parse_args()
    
    # Run the async streaming client
    try:
        asyncio.run(stream_estimation(args.file))
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Stream interrupted by user.[/bold yellow]")
