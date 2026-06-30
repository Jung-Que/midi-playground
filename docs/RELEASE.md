# Windows release process

## Build

Use Python 3.11 with `requirements.txt` installed, then run from the repository root:

```powershell
python tools/build_release.py
```

The command performs these gates in order:

1. Run the complete unit-test suite.
2. Validate and copy only `packaging/release-manifest.txt` resources.
3. Build a flat, portable Windows x64 folder with PyInstaller.
4. Reject settings, logs, exports, local songs, imports, and presets from the package.
5. Launch the packaged integration smoke suite from an unrelated working directory.
6. Confirm packaged settings are written to a temporary user-data directory.
7. Produce a versioned ZIP and matching `.sha256` file in `exports/releases/`.

Use `python tools/build_release.py --check-manifest` for a fast resource-list check. `--skip-tests` is available
for local iteration, but it must not be used for a final release.

## Public resource policy

Only files explicitly listed in `packaging/release-manifest.txt` enter the release. Add a built-in song only after
its redistribution status is confirmed. Personal or rights-unverified material belongs in `songs-local/` and must
not be added to the manifest.

The installed application stores writable data under `%LOCALAPPDATA%/MidiPlayground`:

- `settings.json`
- `songs-local/`
- `presets-local/`
- `logs/`

`MIDI_PLAYGROUND_DATA_DIR` may override this location for portable testing and automated validation.

## Publish checklist

- Run the full release command without `--skip-tests`.
- Extract the ZIP into a fresh directory and start `midi-playground.exe`.
- Verify the SHA-256 value with `Get-FileHash -Algorithm SHA256 <zip>`.
- Review the public song list and license/source metadata.
- Tag the exact release commit and upload both the ZIP and `.sha256` file.

## Long-session validation

Run the accelerated 60-minute playback lifecycle with real bundled MP3/MIDI assets:

```powershell
python tools/soak_test.py --simulated-minutes 60
```

The report is written to `exports/soak/soak-report.json`. It covers repeated song transitions, three square-speed
variants, trajectory continuity, audio decoding, rolling chunk cleanup, map generation latency, note density, and
Python memory growth. This advances the map/audio timeline without waiting an hour of wall-clock time; final release
approval should still include one manual real-time playback session on the target PC.

`.github/workflows/windows-validation.yml` runs the unit suite, source smoke suite, a shorter soak, the verified
PyInstaller build, and packaged smoke suite for every pull request and `master` push.
