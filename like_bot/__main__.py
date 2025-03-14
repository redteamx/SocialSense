"""
Entry point for the like_bot package.

This module invokes the command-line interface for the Instagram Like Bot.
It is executed when the package is run as a script.
"""

from like_bot.cli import cli

def main() -> None:
    """
    Runs the like_bot CLI.
    
    This function serves as the main entry point for the application.
    """
    cli()

if __name__ == "__main__":
    main()

