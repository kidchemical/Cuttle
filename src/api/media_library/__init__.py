"""Media App: every image / video / audio shared in the user's chats.

Read-only index over chat history. Nothing is copied or stored: the source of
truth is each message's markdown / ``<media>`` refs (agents) and
``metadata.attachments`` (user uploads). Files themselves stay where their
owners put them (``<home>/output/uploads/<chat>/`` and the 7-day
``<home>/output/shared/`` staging area owned by :mod:`api.shared_media`), so an
expired staged copy is reported ``available: false`` rather than hidden.

Modules: ``index`` (pure ref extraction + item shaping), ``service`` (user-scoped
DB read + file checks), ``routes`` (``/media_page.html`` + ``/api/media-library``),
``__main__`` (``python -m api.media_library list``). Client: ``src/web/js/media_library/``.
Teardown: delete this package, the client folder, ``media_page.html`` /
``media_page.css``, the 4-line registration in ``web_chat_api.py``, the
``nav-media`` rail entry and the ``galleryFor`` dep in ``chat_media.js``.
"""
