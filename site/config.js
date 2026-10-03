// Deployment configuration for the public VERQIVIA web application.
window.NOTHING_API_BASE = "";
// Set this to the public HTTPS API origin when live read access is deployed.
window.NOTHING_ENROLLMENT_API_BASE = "";
// Wallet access is fail-closed by default. Enable only after deployment security review
// and after the public domain has been manually reviewed for MetaMask/Blockaid safety.
window.NOTHING_WALLET_ENABLED = false;

// Official organization registration is fail-closed until the real API and Google/WALLET auth are configured.
window.NOTHING_IDENTITY_CONTROL_ENABLED = false;
window.NOTHING_GOOGLE_CLIENT_ID = "";
window.NOTHING_WALLET_AUTH_ENABLED = false;
