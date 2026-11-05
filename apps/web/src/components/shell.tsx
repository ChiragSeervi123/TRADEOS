"use client";

import {
  AlertOctagon,
  BarChart3,
  Boxes,
  Building2,
  ClipboardList,
  FileSpreadsheet,
  Inbox,
  LayoutDashboard,
  LogOut,
  Menu,
  Package,
  Plug,
  Scale,
  Settings,
  Share2,
  ShoppingCart,
  Tags,
  Truck,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import { titleCase } from "@/lib/format";
import { cx } from "./ui";

type NavItem = { href: string; label: string; icon: typeof Package; perm?: string; anyPerm?: string[] };

const DISTRIBUTOR_NAV: NavItem[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard, anyPerm: ["reports.read", "orders.read"] },
  { href: "/orders", label: "Orders", icon: ShoppingCart, perm: "orders.read" },
  { href: "/inbox", label: "Order Inbox", icon: Inbox, perm: "orders.read" },
  { href: "/products", label: "Products", icon: Package, perm: "products.read" },
  { href: "/retailers", label: "Retailers", icon: Users, perm: "retailers.read" },
  { href: "/inventory", label: "Inventory", icon: Boxes, perm: "inventory.read" },
  { href: "/schemes", label: "Schemes", icon: Tags, perm: "schemes.read" },
  { href: "/finance", label: "Finance", icon: FileSpreadsheet, perm: "finance.read" },
  { href: "/reconciliation", label: "Reconciliation", icon: Scale, perm: "finance.read" },
  { href: "/fulfilment", label: "Fulfilment", icon: Truck, perm: "fulfilment.read" },
  { href: "/analytics", label: "Analytics", icon: BarChart3, perm: "reports.read" },
  { href: "/exceptions", label: "Exceptions", icon: AlertOctagon, perm: "exceptions.read" },
  { href: "/integrations", label: "Integrations", icon: Plug, perm: "org.read" },
  { href: "/settings", label: "Settings", icon: Settings, perm: "org.read" },
];

const MANUFACTURER_NAV: NavItem[] = [
  { href: "/shared", label: "Shared data", icon: Share2, perm: "shared_data.read" },
  { href: "/settings", label: "Settings", icon: Settings, perm: "org.read" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { me, can, logout, switchOrg } = useAuth();
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const isMfr = me?.organization?.org_type === "manufacturer";
  const nav = (isMfr ? MANUFACTURER_NAV : DISTRIBUTOR_NAV).filter((n) => (n.perm ? can(n.perm) : n.anyPerm ? n.anyPerm.some(can) : true));
  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  const sidebar = (
    <nav aria-label="Main" className="flex h-full flex-col">
      <div className="flex h-14 items-center gap-2 border-b border-white/10 px-4">
        <div className="flex h-7 w-7 items-center justify-center rounded bg-accent font-bold text-white">T</div>
        <span className="text-[15px] font-semibold tracking-wide text-white">TRADEOS</span>
      </div>
      <ul className="flex-1 space-y-0.5 overflow-y-auto p-2">
        {nav.map((n) => (
          <li key={n.href}>
            <Link
              href={n.href}
              onClick={() => setMobileOpen(false)}
              aria-current={active(n.href) ? "page" : undefined}
              className={cx("flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px]", active(n.href) ? "bg-white/12 font-medium text-white" : "text-white/70 hover:bg-white/8 hover:text-white")}
            >
              <n.icon className="h-4 w-4" /> {n.label}
            </Link>
          </li>
        ))}
      </ul>
      <div className="border-t border-white/10 p-3 text-xs text-white/60">
        <p className="truncate font-medium text-white/90">{me?.user.full_name}</p>
        <p className="truncate">{titleCase(me?.role)}</p>
        <button onClick={logout} className="mt-2 inline-flex items-center gap-1.5 text-white/70 hover:text-white">
          <LogOut className="h-3.5 w-3.5" /> Sign out
        </button>
      </div>
    </nav>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 bg-[#13233a] lg:block">{sidebar}</aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileOpen(false)} />
          <aside className="relative h-full w-64 bg-[#13233a]">
            <button aria-label="Close menu" onClick={() => setMobileOpen(false)} className="absolute right-2 top-3 text-white/80">
              <X className="h-5 w-5" />
            </button>
            {sidebar}
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur">
          <button aria-label="Open menu" className="lg:hidden" onClick={() => setMobileOpen(true)}>
            <Menu className="h-5 w-5" />
          </button>
          <Building2 className="hidden h-4 w-4 text-muted sm:block" />
          {me && me.memberships.length > 1 ? (
            <select aria-label="Switch organisation" className="h-8 max-w-[280px] rounded-md border border-border bg-surface px-2 text-sm" value={me.organization?.id ?? ""} onChange={(e) => switchOrg(e.target.value)}>
              {me.memberships.map((m) => (
                <option key={m.organization_id} value={m.organization_id}>
                  {m.organization_name}
                </option>
              ))}
            </select>
          ) : (
            <span className="truncate text-sm font-medium">{me?.organization?.name}</span>
          )}
          <span className="ml-auto hidden items-center gap-1 text-xs text-muted sm:flex">
            <ClipboardList className="h-3.5 w-3.5" /> {me?.user.email}
          </span>
        </header>
        {me?.organization?.is_demo && (
          <div role="note" className="border-b border-accent/30 bg-accent-bg px-4 py-1.5 text-center text-xs font-medium text-accent-strong">
            Demo organisation — all records are synthetic and do not represent real customers, suppliers or market data.
          </div>
        )}
        <main className="mx-auto w-full max-w-[1400px] flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
