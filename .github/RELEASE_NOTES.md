# OpenAVC v0.24.1

- Opening the UI Builder on a system with an admin password no longer
  triggers the browser's native sign-in dialog. The panel canvas makes an
  unauthenticated fallback request whose 401 carried a Basic challenge, which
  Chrome answers with its own prompt over the Programmer. Requests made by
  web pages now receive a plain 401 the application handles itself, while
  direct navigation and command-line clients keep the standard challenge.

Everything else is unchanged from v0.24.0.
