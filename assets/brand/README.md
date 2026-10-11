# Goaletto Telegram branding

Bot: [@GoalettoBot](https://t.me/GoalettoBot), ID 8941820906.
The final full and short English descriptions are in telegram-profile.json.
They are applied both as the default text and for language_code=en.

Icon: goaletto-icon.png is the original built-in Imagegen output.
goaletto-icon.jpg is the JPEG export uploaded to Telegram.
The prompt is preserved in icon-prompt.txt.
Style: approved C — Dugout, burgundy, warm ivory paper and moss.
No real club logos or player likenesses.

Applied on 2026-10-11; default/English descriptions and profile photo were read back
successfully through the official Telegram Bot API.

To reapply from the repository root:

```bash
python scripts/configure_telegram_profile.py --apply-icon
```

The script reads GOALETTO_BOT_TOKEN from the ignored local .env and verifies the bot
identity before making changes. Never put the token in this directory, commits or logs.
Local .env permissions are 0600. The verification receipt/download stay under ignored work/.
