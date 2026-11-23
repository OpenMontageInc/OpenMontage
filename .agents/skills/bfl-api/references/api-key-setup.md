---
name: api-key-setup
description: How to obtain and configure a BFL API key
---

# API Key Setup

> **Important:** Always verify your API key before attempting image generation. Missing or invalid keys result in "Not authenticated" errors.

## Quick Validation

Run this first to check if your key is configured and valid:

```bash
# Check if key is set
[ -z "$BFL_API_KEY" ] && echo "Error: BFL_API_KEY not set" || echo "OK: Key configured"
```

If not set, follow the steps below.

## Get a Key

1. Go to **https://dashboard.bfl.ai/get-started**
2. Click **"Create Key"**
3. Select organization (ask user if multiple options)
4. Copy the key (starts with `bfl_`)

## For Agents Making Direct API Calls

When `BFL_API_KEY` is not set in the current session:

1. **Check for existing `.env`**:
   ```bash
   grep BFL_API_KEY .env 2>/dev/null
   ```

2. **If found, export it**:
   ```bash
   export BFL_API_KEY=$(grep '^BFL_API_KEY=' .env | cut -d '=' -f2)
   ```

3. **If not found, direct the user to update `.env` directly**:
   > "A BFL API key is required to generate images. Please:
   > 1. Get a key from https://dashboard.bfl.ai/get-started
   > 2. Add `BFL_API_KEY=your_key_here` to your `.env` file
   > (Do not send your API key in chat.)"

4. **Ensure `.env` is ignored and export the key**:
   ```bash
   echo '.env' >> .gitignore
   export BFL_API_KEY=$(grep '^BFL_API_KEY=' .env | cut -d '=' -f2)
   ```

Now `$BFL_API_KEY` is available for direct curl/API calls in the session.
