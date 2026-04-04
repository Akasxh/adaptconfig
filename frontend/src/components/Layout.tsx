import clsx from "clsx";
import {
  FileText,
  FlaskConical,
  LayoutDashboard,
  Menu,
  Plug,
  Search,
  Settings,
  Shield,
  Webhook,
  X,
  Zap,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

const navItems = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/adapters", icon: Plug, label: "Adapters" },
  { to: "/documents", icon: FileText, label: "Documents" },
  { to: "/configurations", icon: Settings, label: "Configurations" },
  { to: "/simulations", icon: FlaskConical, label: "Simulations" },
  { to: "/audit", icon: Shield, label: "Audit Log" },
  { to: "/search", icon: Search, label: "Search" },
  { to: "/webhooks", icon: Webhook, label: "Webhooks" },
] as const;

export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={() => setSidebarOpen(false)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setSidebarOpen(false);
          }}
          role="button"
          tabIndex={0}
          aria-label="Close sidebar"
        />
      )}

      {/* Sidebar */}
      <aside
        className={clsx(
          "fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-gray-800 bg-gray-950 transition-transform duration-200 lg:static lg:translate-x-0",
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        {/* Logo */}
        <div className="flex h-16 items-center gap-3 border-b border-gray-800 px-6">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600">
            <Zap className="h-4 w-4 text-white" />
          </div>
          <span className="text-lg font-bold tracking-tight text-white">FinSpark</span>
          <button
            type="button"
            className="ml-auto lg:hidden"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close sidebar"
          >
            <X className="h-5 w-5 text-gray-400" />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 space-y-1 px-3 py-4">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-indigo-600/10 text-indigo-400"
                    : "text-gray-400 hover:bg-gray-800/60 hover:text-gray-200"
                )
              }
            >
              <Icon className="h-4.5 w-4.5 shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div className="border-t border-gray-800 px-6 py-4">
          <p className="text-xs text-gray-500">FinSpark v0.1.0</p>
          <p className="text-xs text-gray-600">Enterprise Integration Platform</p>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-16 shrink-0 items-center gap-4 border-b border-gray-800 bg-gray-950/80 px-6 backdrop-blur-sm">
          <button
            type="button"
            className="lg:hidden"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open navigation menu"
          >
            <Menu className="h-5 w-5 text-gray-400" />
          </button>
          <div className="flex-1" />
          <div className="flex items-center gap-3">
            <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-xs text-gray-400">System Healthy</span>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-4 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
