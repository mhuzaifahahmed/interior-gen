// Loads the Clerk JS SDK via its own CDN (no npm/bundler - same convention
// as Tailwind/GSAP elsewhere in this project, see CLAUDE.md's "Frontend"
// section) and exposes a single `clerkReady` promise other inline scripts
// can await before touching `window.Clerk`. Shared by index.html,
// login.html, and signup.html so the bootstrap logic lives in one place.
//
// The publishable key is safe to hardcode here - it's meant to be public
// (unlike CLERK_SECRET_KEY, which stays backend-only in .env). The Frontend
// API domain Clerk's script tag needs is decoded from the key itself rather
// than duplicated as a second hardcoded value - a Clerk publishable key is
// "pk_test_" or "pk_live_" followed by base64(`${frontendApiDomain}$`), so
// switching Clerk instances only ever means updating CLERK_PUBLISHABLE_KEY.
const CLERK_PUBLISHABLE_KEY = "pk_test_ZXRoaWNhbC1nb2F0LTg2LmNsZXJrLmFjY291bnRzLmRldiQ";

function decodeClerkFrontendApi(publishableKey) {
  const encoded = publishableKey.replace(/^pk_(test|live)_/, "");
  return atob(encoded).replace(/\$$/, "");
}

const CLERK_FRONTEND_API = decodeClerkFrontendApi(CLERK_PUBLISHABLE_KEY);

function loadScript({ src, attrs = {} }) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.defer = true;
    script.crossOrigin = "anonymous";
    for (const [name, value] of Object.entries(attrs)) script.setAttribute(name, value);
    script.src = src;
    script.addEventListener("load", resolve);
    script.addEventListener("error", () => reject(new Error(`Failed to load script: ${src}`)));
    document.head.appendChild(script);
  });
}

// clerk-js's mountSignIn()/mountSignUp() need the @clerk/ui components chunk
// loaded FIRST, as its own separate script - loading clerk-js alone throws
// "Clerk was not loaded with Ui components" at mount time (confirmed via a
// live local test: the widgets never rendered, this exact error appeared in
// console). Both are documented as required together in Clerk's own
// no-bundler quickstart.
const clerkReady = (async () => {
  await loadScript({
    src: `https://${CLERK_FRONTEND_API}/npm/@clerk/ui@1/dist/ui.browser.js`,
  });
  await loadScript({
    src: `https://${CLERK_FRONTEND_API}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`,
    attrs: { "data-clerk-publishable-key": CLERK_PUBLISHABLE_KEY },
  });
  await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor } });
  return window.Clerk;
})();
