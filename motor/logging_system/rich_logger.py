"""
Sistema de logging con Rich para salida bonita en terminal
"""
import time
from datetime import datetime
from typing import Optional, Dict, Any
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from rich import box

console = Console()


class RichLogger:
    """Logger con salida bonita usando Rich"""
    
    def __init__(self, email_id: str = "", verbose: bool = True):
        self.email_id = email_id
        self.verbose = verbose
        self.start_time = time.time()
        self.step_times: Dict[str, float] = {}
        self.current_step: Optional[str] = None
        
    def start(self, email_id: str, subject: str = ""):
        """Inicia el procesamiento mostrando encabezado"""
        self.email_id = email_id
        self.start_time = time.time()
        
        if self.verbose:
            header = Text()
            header.append("PQRD Radicacion\n", style="bold cyan")
            header.append(f"ID: {email_id[:50]}...\n", style="dim")
            header.append(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", style="dim")
            
            console.print(Panel(header, box=box.ROUNDED, border_style="cyan"))
            console.print()
    
    def step_start(self, step_name: str, message: str = ""):
        """Inicia un paso del proceso"""
        self.current_step = step_name
        self.step_times[step_name] = time.time()
        
        if self.verbose:
            console.print(f"  [yellow]●[/yellow] {datetime.now().strftime('%H:%M:%S')} [bold]{step_name}[/bold]: {message}")
    
    def step_success(self, message: str = "", data: Optional[Dict[str, Any]] = None):
        """Marca un paso como exitoso"""
        elapsed = 0
        if self.current_step and self.current_step in self.step_times:
            elapsed = int((time.time() - self.step_times[self.current_step]) * 1000)
        
        if self.verbose:
            status_line = f"  [green]✓[/green] {datetime.now().strftime('%H:%M:%S')} [bold]{self.current_step}[/bold]: {message} [dim]({elapsed}ms)[/dim]"
            console.print(status_line)
            
            if data:
                for key, value in data.items():
                    console.print(f"      [dim]{key}:[/dim] {value}")
    
    def step_error(self, message: str, data: Optional[Dict[str, Any]] = None):
        """Marca un paso con error"""
        if self.verbose:
            console.print(f"  [red]✗[/red] {datetime.now().strftime('%H:%M:%S')} [bold red]{self.current_step}[/bold red]: {message}")
            
            if data:
                for key, value in data.items():
                    console.print(f"      [dim red]{key}:[/dim red] {value}")
    
    def step_warning(self, message: str, data: Optional[Dict[str, Any]] = None):
        """Muestra una advertencia"""
        if self.verbose:
            console.print(f"  [yellow]![/yellow] {datetime.now().strftime('%H:%M:%S')} [bold yellow]{self.current_step}[/bold yellow]: {message}")

            if data:
                for key, value in data.items():
                    console.print(f"      [dim yellow]{key}:[/dim yellow] {value}")
    
    def step_info(self, message: str, data: Optional[Dict[str, Any]] = None):
        """Muestra informacion"""
        if self.verbose:
            console.print(f"  [blue]ℹ[/blue] {datetime.now().strftime('%H:%M:%S')} [bold]{self.current_step}[/bold]: {message}")
            
            if data:
                for key, value in data.items():
                    console.print(f"      [dim]{key}:[/dim] {value}")
    
    def finish_success(self, radicado: str, duration_ms: int):
        """Muestra resumen de exito"""
        if self.verbose:
            console.print()
            result = Text()
            result.append("RADICACION EXITOSA\n", style="bold green")
            result.append(f"Numero: {radicado}\n", style="white")
            result.append(f"Duracion: {duration_ms}ms", style="dim")
            
            console.print(Panel(result, box=box.ROUNDED, border_style="green"))
    
    def finish_error(self, error: str, duration_ms: int):
        """Muestra resumen de error"""
        if self.verbose:
            console.print()
            result = Text()
            result.append("ERROR EN RADICACION\n", style="bold red")
            result.append(f"Error: {error}\n", style="white")
            result.append(f"Duracion: {duration_ms}ms", style="dim")
            
            console.print(Panel(result, box=box.ROUNDED, border_style="red"))
    
    def finish_duplicate(self, duration_ms: int):
        """Muestra que fue duplicado"""
        if self.verbose:
            console.print()
            result = Text()
            result.append("CORREO DUPLICADO\n", style="bold yellow")
            result.append("El correo ya fue procesado anteriormente\n", style="white")
            result.append(f"Duracion: {duration_ms}ms", style="dim")
            
            console.print(Panel(result, box=box.ROUNDED, border_style="yellow"))
    
    def show_extracted_data(self, data: Dict[str, Any]):
        """Muestra los datos extraidos en una tabla"""
        if self.verbose:
            table = Table(title="Datos Extraidos por IA", box=box.ROUNDED)
            table.add_column("Campo", style="cyan")
            table.add_column("Valor", style="white")
            
            for key, value in data.items():
                table.add_row(str(key), str(value))
            
            console.print()
            console.print(table)
            console.print()
