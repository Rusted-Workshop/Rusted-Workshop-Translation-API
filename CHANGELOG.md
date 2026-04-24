# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Multi-stage Dockerfiles for all four components (API / Coordinator / File Worker / Cleanup).
- GitHub Actions workflow that builds & publishes multi-arch (amd64 + arm64) images to GHCR on every `v*.*.*` tag.
- `docker-compose.prod.yml` ready-to-use deployment manifest pulling images from GHCR.
- `RELEASE.md` with full release playbook.
- `README.md` with architecture overview and deployment guide.
- `.dockerignore` to keep build context minimal.

### Changed
- All Dockerfiles now use non-root `app` user and dedicated `/opt/venv` virtualenv.
- API image ships with a `curl`-based healthcheck on `/health`.

### Security
- Secrets and `.env` files are now explicitly excluded from Docker build context.

---

## [0.1.0] - TBD

### Added
- Initial public release scaffolding.

[Unreleased]: https://github.com/Rusted-Workshop/Rusted-Workshop-Translation-API/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Rusted-Workshop/Rusted-Workshop-Translation-API/releases/tag/v0.1.0
