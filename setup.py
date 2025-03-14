from setuptools import setup, find_packages
import os

# Read the long description from README.md if it exists.
here = os.path.abspath(os.path.dirname(__file__))
try:
    with open(os.path.join(here, "README.md"), encoding="utf-8") as f:
        long_description = f.read()
except FileNotFoundError:
    long_description = ""

setup(
    name="like_bot",
    version="1.0.0",  # Using semantic versioning.
    description="Instagram Like Bot: Automate liking and follower tracking for Instagram profiles.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Your Name",  # Replace with your name
    author_email="your.email@example.com",  # Replace with your email
    url="https://github.com/yourusername/like_bot",  # Replace with your project's URL
    packages=find_packages(),
    install_requires=[
        "asyncpg",
        "aiograpi",
        "click",
        "instaloader",
        "rich",
        "aiohttp"
    ],
    python_requires=">=3.10",
    classifiers=[
        "Programming Language :: Python :: 3.10",
        "Operating System :: OS Independent",
        "License :: OSI Approved :: MIT License",  # Adjust if using a different license
        "Topic :: Software Development :: Libraries",
        "Topic :: Internet :: WWW/HTTP",
    ],
    entry_points={
        "console_scripts": [
            "like_bot = like_bot.__main__:main"
        ]
    },
    include_package_data=True,
)

