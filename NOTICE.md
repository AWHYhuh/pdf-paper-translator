# Third-party notice

This application installs and invokes the following upstream projects:

- `pdf2zh-next 2.9.0`
- `BabelDOC 0.6.2`

The installed package metadata declares both projects under `AGPL-3.0`.
They are not bundled in the source ZIP; `install.ps1` downloads them into the
application-local `.venv` directory.

The translation provider is DeepSeek. Paper text selected for translation is
sent to the DeepSeek API. Review the upstream licenses and provider terms before
redistributing or operating this package as a public service.
