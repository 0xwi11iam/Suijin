/**
 * Suijin Home — bootstrap for the new-engagement landing (`zero/home.html`).
 *
 * The landing is 0's native new-thread screen (ui/backdrops + the composer shell from
 * packages/web/src/console/conversation.tsx), carried in `zero/src` and adapted in
 * `suijin-home.tsx`. It runs in its own document so 0's stylesheet can never reach the console,
 * and talks to the console only through the messages in `protocol.ts`.
 *
 * Bootstrap mirrors 0's own main.tsx (tooltip provider + error boundary); the theme script lives
 * in home.html so the palette is right before first paint.
 */
import "@zero/styles.css";
import "@zero/color-themes.css";
import "./home.css";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { TooltipProvider } from "@zero/ui/components/tooltip";
import { SuijinHome } from "./suijin-home";

/** Keeps a render failure from blanking the frame: the operator sees the error instead of an empty pane. */
class HomeErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Suijin Home failed to render", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex min-h-screen items-center justify-center p-6 text-center text-sm text-muted-foreground-1">
        <div>
          <p className="text-foreground">The new-engagement screen hit an error.</p>
          <p className="mt-1 break-words">{String(this.state.error.message || this.state.error)}</p>
        </div>
      </div>
    );
  }
}

createRoot(document.getElementById("root")!).render(
  <HomeErrorBoundary>
    <TooltipProvider>
      <SuijinHome />
    </TooltipProvider>
  </HomeErrorBoundary>,
);
