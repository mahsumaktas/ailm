# Contributing to ailm

Thank you for your interest in contributing.

ailm is in pre-alpha. The codebase is changing rapidly.
The best way to contribute right now is through feedback and discussion.

---

## Ways to Contribute

### Right Now (Pre-alpha)
- **Use it and report** — install, run for a week, open issues
- **Discuss architecture** — open a Discussion before writing code
- **Review the roadmap** — does this solve your problem?
- **Test on your distro** — especially non-CachyOS Arch-based

### When Alpha Ships
- Bug fixes with reproduction cases
- New distro backends (Fedora, openSUSE)
- New hook types (TOML-defined)
- Documentation improvements
- Translation (TR → EN and vice versa)

### When Beta Ships
- Plugin development
- Performance profiling
- Security review

---

## Development Setup

```bash
git clone https://github.com/yourusername/ailm
cd ailm
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Required: Ollama running with these models
ollama pull qwen3.5:9b
ollama pull nomic-embed-text
```

## Code Style

- Python 3.11+
- Type hints everywhere
- `ruff` for linting
- `mypy` for type checking
- `pytest` for tests

## Commit Messages

```
feat: add journald pre-filter pipeline
fix: prevent queue overflow when ollama is unavailable
docs: add memory system architecture diagram
refactor: extract PackageManager into Protocol
```

## Opening Issues

**Bug reports:** Include OS, Python version, Ollama version,
full traceback, and steps to reproduce.

**Feature requests:** Explain the use case first, then the
proposed solution. "I want X because Y" before "please add Z."

**Architecture discussions:** Use GitHub Discussions, not Issues.

---

## Security

If you find a security issue (especially around prompt injection
or command execution), please email privately rather than opening
a public issue.

---

## License

By contributing, you agree your contributions will be licensed
under the MIT license.
