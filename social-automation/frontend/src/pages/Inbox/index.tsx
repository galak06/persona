/**
 * Approval Inbox page. The route body — SideNav lives in `App.tsx`.
 *
 * Phase 5: a single `PendingTab` (history/decided tab is out of scope
 * for the MVP; everything in this UI is "pending right now"). Future
 * phases can wrap PendingTab in a tab strip without changing the route.
 */

import PendingTab from "./PendingTab";

export default function Inbox(): React.JSX.Element {
  return <PendingTab />;
}
