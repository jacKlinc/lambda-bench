import typer

app = typer.Typer()


@app.command()
def run() -> None:
    raise NotImplementedError


@app.command()
def plot() -> None:
    raise NotImplementedError


@app.command()
def compare() -> None:
    raise NotImplementedError
