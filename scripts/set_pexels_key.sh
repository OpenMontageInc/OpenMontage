#!/bin/zsh
# Prompts for the Pexels API key (hidden input) and stores it in .env.
# Rejects anything that is not plain ASCII letters/digits, so a mistyped
# value (e.g. typed with a non-English keyboard layout) never lands in .env.
cd "${0:A:h}/.." || exit 1
read -rs "k?Paste your Pexels API key (Cmd+V), then press Enter: "
echo
if [[ ! "$k" =~ '^[A-Za-z0-9]{30,}$' ]]; then
  echo "NOT saved: that doesn't look like a Pexels key (expected ~56 English letters/digits)."
  echo "Copy the key from https://www.pexels.com/api/ (under 'Your API Key') and paste it with Cmd+V."
  unset k
  exit 1
fi
sed -i '' "s|^PEXELS_API_KEY=.*|PEXELS_API_KEY=$k|" .env
unset k
echo "saved"
