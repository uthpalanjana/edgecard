"""
cli/main.py — ContextPack CLI entry point.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml

from contextpack.card import Card
from contextpack.encoders import Encoding, get_encoder
from contextpack.validator import CardValidationError, Validator


@click.group()
@click.version_option(version="0.1.0", prog_name="contextpack")
def cli():
    """ContextPack SDK — build, validate, and encode AI context cards."""


@cli.command("validate")
@click.argument("card_path", type=click.Path(exists=True, path_type=Path))
def validate_cmd(card_path: Path):
    """Validate a .card.yaml file against the schema."""
    try:
        with open(card_path) as fh:
            data = yaml.safe_load(fh)
        validator = Validator()
        validator.validate_dict(data)
        click.echo(f"✓ {card_path.name} is valid")
    except CardValidationError as exc:
        click.echo(f"✗ Validation failed: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"✗ Error: {exc}", err=True)
        sys.exit(1)


@cli.command("encode")
@click.argument("card_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--format", "-f",
    type=click.Choice(["yaml", "json", "toon", "text"], case_sensitive=False),
    default="toon",
    show_default=True,
    help="Output encoding format.",
)
def encode_cmd(card_path: Path, format: str):
    """Encode a card to the specified format and print to stdout."""
    try:
        with open(card_path) as fh:
            data = yaml.safe_load(fh)
        card = Card(**data)
        enc = get_encoder(format)
        click.echo(enc.encode(card))
    except Exception as exc:
        click.echo(f"✗ Error: {exc}", err=True)
        sys.exit(1)


@cli.command("inspect")
@click.argument("card_path", type=click.Path(exists=True, path_type=Path))
def inspect_cmd(card_path: Path):
    """Print summary information about a card."""
    try:
        with open(card_path) as fh:
            data = yaml.safe_load(fh)
        card = Card(**data)
        click.echo(f"Card ID:     {card.card_id}")
        click.echo(f"Type:        {card.card_type}")
        click.echo(f"Subject:     {card.identity.subject}")
        click.echo(f"Location:    {card.identity.location}")
        click.echo(f"Generated:   {card.generated_at}")
        click.echo(f"Readings:    {len(card.state.readings)}")
        click.echo(f"Thresholds:  {len(card.knowledge.thresholds)}")
        click.echo(f"Facts:       {len(card.knowledge.domain_facts)}")
        click.echo(f"Events:      {len(card.history.events)}")
    except Exception as exc:
        click.echo(f"✗ Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
