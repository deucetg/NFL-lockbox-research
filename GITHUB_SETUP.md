# GitHub setup

Repository: https://github.com/deucetg/NFL-lockbox-research

## Upload
1. Download and extract NFL_LOCKBOX_GITHUB_READY.zip.
2. In the repository on GitHub, choose Add file > Upload files.
3. Upload the extracted files (including play_by_play_2026.csv.gz and nfl_lockbox_2026.sqlite).
4. Commit to main. Do not upload any secrets or private data.

## Rebuild locally
Run `python build_lockbox.py` in a directory containing `play_by_play_2026.csv.gz`.

## Provenance and limitations
User-uploaded nflverse-format play-by-play. Original ESPN attribution unconfirmed. This database is research-only and needs box-score reconciliation, pass-attempt classification, and two-point validation.

## Future GitHub Actions
Do not schedule an automatic external player-stat download until the user explicitly approves changing the user-uploaded-only source policy.
