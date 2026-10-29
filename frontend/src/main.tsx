import React from "react";
import { createRoot } from "react-dom/client";
import App from "./MatOptApp";
import { ZCodeIntlProvider } from "./zcode/i18n/IntlProvider";
import { TooltipProvider } from "./zcode/components/ui/tooltip";
import { LucideProvider } from "lucide-react";
import "@fontsource-variable/inter";
import "@fontsource-variable/newsreader/opsz.css";
import "@fontsource-variable/newsreader/opsz-italic.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./zcode/styles.css";
import "./matopt.css";
import "./styles/design-tokens.css";
import { bootTheme } from "./utils/theme";
import { bootUiFontSize, subscribeToUiFontSizeStorageChanges } from "./utils/uiFontSize";
import "katex/dist/katex.min.css";

// Apply the persisted palette and interface font size before the first render,
// so the app never paints at the wrong scale and never flashes the wrong theme.
// `index.html` carries the matching pre-paint script for the interval before
// this module executes.
bootTheme();
bootUiFontSize();
// Process-lifetime subscription: mirrors a font-size change made in another
// tab. Nothing here unmounts, so there is nothing to unsubscribe.
subscribeToUiFontSizeStorageChanges();

createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
        <ZCodeIntlProvider initialLocale="en-US"><TooltipProvider><LucideProvider strokeWidth={1.5}><App /></LucideProvider></TooltipProvider></ZCodeIntlProvider>
    </React.StrictMode>
);
