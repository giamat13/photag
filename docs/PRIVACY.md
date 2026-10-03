# photag privacy policy

photag is a photo manager that runs entirely on your own computer. It has **no accounts, no analytics and no telemetry**, and its author runs
no server that receives anything from it. Your photos, the catalog (including EXIF, captions, tags, faces and edit settings), backups and
settings are stored on your computer (in the library folder you choose and in your Windows user profile) and are not sent anywhere on their own.

## When photag uses the internet
Only in these cases, each of which you start or switch on yourself:

| What | Where it connects | What is sent |
|---|---|---|
| Update check (once a day; can be turned off in Preferences, or run by hand from the Help menu) | GitHub (`api.github.com`, `github.com`) | an ordinary request for this project's latest release; nothing about you or your photos |
| Downloading an update | GitHub (`github.com`) | an ordinary file download |
| Face detection, first use | the model download of the InsightFace library (about 300 MB, one time) | an ordinary file download; the photos themselves are analysed on your computer |
| Search by meaning, first use (only after you agree) | Hugging Face (`huggingface.co`) | an ordinary file download (about 600 MB, one time); the analysis itself is local |
| AI tagging (only when you start it, with a provider and key you chose) | OpenAI, Anthropic, Google (Gemini) or OpenRouter, whichever you selected | small thumbnails of the photos you chose, plus the key you entered; subject to that provider's own privacy policy |
| triplan connection (only when you connect your account) | Google Firebase (`identitytoolkit.googleapis.com`, `firestore.googleapis.com`) and triplan | your triplan e-mail and password to sign in, and the trip links you create; subject to Google's and triplan's policies |
| Opening a link (release page, HandBrake download page, triplan) | your web browser | whatever your browser sends when you open a page |

## How secrets are kept
The AI keys and the triplan password you enter are stored encrypted with Windows DPAPI, readable only by your Windows user on this computer.

## Deleting your data
Everything photag stores is in your library folder and in `%APPDATA%\photag` / `%LOCALAPPDATA%\photag`; deleting those removes it. Uninstalling
photag never deletes your photos or catalog.

## Questions
Open an issue at <https://github.com/giamat13/photag/issues>.
