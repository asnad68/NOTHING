# Chrome installation — NOTHING

## Important

Chrome's **Load unpacked** installer expects a folder containing `manifest.json`. It does not accept a ZIP file directly.

## Install

1. Download `nothing-business-identity-chrome-0.1.0.zip`.
2. Extract it to a normal folder, for example `C:\\NOTHING-CHROME\\`.
3. Open Chrome and go to `chrome://extensions`.
4. Turn on **Developer mode**.
5. Click **Load unpacked**.
6. Select the extracted folder that directly contains:
   - `manifest.json`
   - `popup.html`
   - `popup.js`
   - `options.html`
   - `icons/`
7. Chrome should add **NOTHING — Business Identity**.
8. Use the puzzle icon to pin NOTHING to the toolbar.

## Common mistake

Do **not** select:

- the ZIP file itself;
- the outer `NOTHING-browser-extension-0.1.0-bundle` folder;
- the bundle's parent folder.

The selected folder must show `manifest.json` immediately inside it.

## First test

Open a normal HTTPS website, click the NOTHING extension icon, and click **Inspect current page**. The extension should read the page metadata and fill the draft form.

The registration payment flow remains deployment-gated until the public enrollment API is configured.
