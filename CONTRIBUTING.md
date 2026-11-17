# Contributing to Campus Barter

Thanks for wanting to help! Small fixes and big ideas are both welcome.

## Ways to help
- Report a bug or suggest a feature by opening an [issue](../../issues/new/choose)
- Improve the documentation
- Fix a bug or build a feature with a pull request

## Set up on your computer
1. Click **Fork** on GitHub, then clone your fork.
2. Create a virtual environment and install everything:
   ```bash
   python -m venv .venv
   # Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
   pip install -r requirements-dev.txt
   flask --app app migrate
   flask --app app seed
   flask --app app run
   ```
3. Open http://127.0.0.1:5000

## Making a change
1. Create a branch: `git checkout -b my-change`
2. Make your change. Keep it small and focused.
3. Run the tests: `python -m unittest discover -s tests` (about 5 minutes).
4. Add a line to [CHANGELOG.md](CHANGELOG.md) under **Unreleased** if behaviour, the database or the setup changed.
5. Push the branch and open a pull request.

## Ground rules
- **No new dependencies** unless there is a strong reason. Say why in the pull request.
- **No build step.** The front end is plain CSS and plain JavaScript.
- **SQL lives in the `*_service.py` files**, never inside route functions, and always uses `?` parameters, never string building.
- **A new route needs a row in the permission tests** (`tests/test_permission_matrix.py`).
- **A database change needs a new file in `migrations/`.** Never edit an old one.
- **Contact details must never appear before a request is accepted.**

The how-to guides in [docs/how-to/](docs/how-to/) walk through the common changes.

## Commit messages
Write a short sentence that says what changed, for example `Fix search with a percent sign`.

## Be kind
Everyone here follows our [Code of Conduct](CODE_OF_CONDUCT.md).
