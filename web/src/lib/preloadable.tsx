import { lazy, type FunctionComponent } from "react";

/**
 * A lazy route that stops suspending once its chunk has loaded.
 *
 * Router history updates run outside transitions (see main.tsx), so a plain
 * `lazy()` page would show the Suspense fallback on its first visit. React 18
 * suspends even on an already-resolved import, so the loaded component is
 * rendered directly instead.
 */
export function preloadable<P extends object>(load: () => Promise<FunctionComponent<P>>) {
  let loaded: FunctionComponent<P> | null = null;
  let pending: Promise<{ default: FunctionComponent<P> }> | null = null;
  const preload = () => {
    pending ??= load().then(component => {
      loaded = component;
      return { default: component };
    });
    return pending;
  };
  // React types cannot resolve PropsWithRef<P> for a generic P; the lazy wrapper
  // takes exactly the props of the component it loads.
  const Lazy = lazy(preload) as unknown as FunctionComponent<P>;
  function Route(props: P) {
    const Loaded = loaded;
    return Loaded ? <Loaded {...props} /> : <Lazy {...props} />;
  }
  return Object.assign(Route, { preload });
}
