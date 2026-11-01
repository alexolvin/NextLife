# Contributing to NextLife

Thank you for your interest in contributing to NextLife!

## Getting Started

1. Fork the repository and clone it locally.
2. Ensure you have Python 3.12+ installed.
3. Install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```
4. Copy `.env.example` to `.env` and fill in local test values.

## Development Workflow

- Create a feature branch: `git checkout -b feat/your-feature-name`.
- Run tests before committing:
  ```bash
  pytest
  ```
- Check code style and formatting:
  ```bash
  ruff check .
  ruff format --check .
  ```
- Commit using Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`).
- Open a Pull Request on GitHub.

## Security and Privacy Guidelines

- Never commit secrets, tokens, personal identification data, or private network addresses.
- All configuration examples must use placeholders only.
