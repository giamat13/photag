# Problem reports ("Help > Report a problem…")

The program can create a GitHub issue for the user, who then needs no GitHub account. How it works and how to switch it on:

1. **The bot account.** Make a normal GitHub account only for this (for example `photag-issues`). It owns nothing and is not a collaborator.
2. **Its token.** Settings > Developer settings > Personal access tokens > *Tokens (classic)* > scope **`public_repo` only**, no expiry (or a long one).
3. **The secret.** In the photag repository: Settings > Secrets and variables > Actions > New repository secret, name **`REPORT_TOKEN`**, value the token.
4. Release as usual. The release workflow runs `tools/write_report_token.py`, which writes `app/_report_token.py` (not in git) before the program is built; it is part of the installer, the portable ZIP, the MSI, the code update and the Linux / macOS packages.

Without the secret nothing breaks: the button opens GitHub's own "new issue" page with the text filled in (the user needs a GitHub account to finish it).

* The issues appear as opened by the bot, titled `[Report] …`, labelled `user-report` (GitHub ignores the label when the bot may not label; the title says it anyway).
* The token can be extracted from the program by someone who tries. It can only open issues, so the worst case is spam: delete the token, make a new one, update the secret and release. Each installation sends at most 3 reports an hour and 10 a day.
* Reports are public: the dialog says so, shows the exact text, and removes folders, the user name, e-mail and IP addresses from the log lines.
