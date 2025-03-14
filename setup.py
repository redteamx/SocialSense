from setuptools import setup, find_packages
import os
from typing import Optional


def read_file(file_path: str, default: str = "") -> str:
    """
    Read the contents of a file, returning a default value if the file is not found.

    Args:
        file_path (str): Path to the file to read.
        default (str): Value to return if the file cannot be read. Defaults to an empty string.

    Returns:
        str: Contents of the file or the default value if reading fails.
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, IOError) as e:
        print(f"Warning: Could not read {file_path}: {str(e)}", file=sys.stderr)
        return default


# Project metadata as constants for better maintainability
PROJECT_NAME = "like_bot"
VERSION = "1.0.0"  # Semantic versioning: major.minor.patch
DESCRIPTION = "Instagram Like Bot: Automate liking and follower tracking for Instagram profiles."
AUTHOR = "Your Name"  # Replace with your name
AUTHOR_EMAIL = "your.email@example.com"  # Replace with your email
PROJECT_URL = "https://github.com/yourusername/like_bot"  # Replace with your project's URL
LICENSE = "MIT"  # Adjust if using a different license

# Installation requirements
INSTALL_REQUIRES = [
    "asyncpg>=0.30.0",
    "aiograpi>=0.0.3",
    "click>=8.1.8",
    "instaloader>=4.14.1",
    "rich>=13.9.4",
    "aiohttp>=3.11.13",
]

# Classifiers for PyPI
CLASSIFIERS = [
    "Programming Language :: Python :: 3.10",
    "Operating System :: OS Independent",
    f"License :: OSI Approved :: {LICENSE} License",
    "Topic :: Software Development :: Libraries",
    "Topic :: Internet :: WWW/HTTP",
]

# Entry points for command-line scripts
ENTRY_POINTS = {
    "console_scripts": [
        f"{PROJECT_NAME} = like_bot.__main__:main",
    ],
}

# Determine the project root directory
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))

# Read long description from README.md
LONG_DESCRIPTION = read_file(os.path.join(PROJECT_ROOT, "README.md"))

# Setup configuration
setup(
    name=PROJECT_NAME,
    version=VERSION,
    description=DESCRIPTION,
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    author=AUTHOR,
    author_email=AUTHOR_EMAIL,
    url=PROJECT_URL,
    packages=find_packages(where=".", exclude=("tests", "docs")),
    install_requires=INSTALL_REQUIRES,
    python_requires=">=3.10",
    classifiers=CLASSIFIERS,
    entry_points=ENTRY_POINTS,
    include_package_data=True,
)
