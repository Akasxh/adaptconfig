import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Layout from "../Layout";

vi.mock("react-router-dom", () => ({
  NavLink: ({ to, children, className, style }: { to: string; children: React.ReactNode; className: (opts: { isActive: boolean }) => string; style: (opts: { isActive: boolean }) => React.CSSProperties; end?: boolean; onClick?: () => void }) => {
    const cls = typeof className === "function" ? className({ isActive: false }) : className;
    const sty = typeof style === "function" ? style({ isActive: false }) : style;
    return <a href={to} className={cls} style={sty}>{children}</a>;
  },
  Outlet: () => <div data-testid="outlet" />,
  useNavigate: () => vi.fn(),
}));

describe("Layout", () => {
  it("renders the AdaptConfig brand name", () => {
    render(<Layout />);
    expect(screen.getByText("AdaptConfig")).toBeInTheDocument();
  });

  it("renders all navigation group labels", () => {
    render(<Layout />);
    expect(screen.getByText("Core")).toBeInTheDocument();
    expect(screen.getByText("Integrations")).toBeInTheDocument();
    expect(screen.getByText("Governance")).toBeInTheDocument();
  });

  it("renders all nav items", () => {
    render(<Layout />);
    expect(screen.getByRole("link", { name: /dashboard/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /documents/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /search/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /adapters/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /configurations/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /webhooks/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /simulations/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /audit log/i })).toBeInTheDocument();
  });

  it("nav items have correct hrefs", () => {
    render(<Layout />);
    expect(screen.getByRole("link", { name: /dashboard/i })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /documents/i })).toHaveAttribute("href", "/documents");
    expect(screen.getByRole("link", { name: /adapters/i })).toHaveAttribute("href", "/adapters");
  });

  it("renders the Outlet for page content", () => {
    render(<Layout />);
    expect(screen.getByTestId("outlet")).toBeInTheDocument();
  });

  it("renders mobile menu toggle button", () => {
    render(<Layout />);
    expect(screen.getByRole("button", { name: "Open navigation menu" })).toBeInTheDocument();
  });

  it("opens sidebar when mobile menu button is clicked", async () => {
    render(<Layout />);
    const sidebar = screen.getByRole("complementary");
    expect(sidebar).not.toHaveClass("translate-x-0");

    await userEvent.click(screen.getByRole("button", { name: "Open navigation menu" }));
    expect(sidebar).toHaveClass("translate-x-0");
  });

  it("closes sidebar when close button inside sidebar is clicked", async () => {
    render(<Layout />);
    await userEvent.click(screen.getByRole("button", { name: "Open navigation menu" }));

    const closeButtons = screen.getAllByRole("button", { name: "Close sidebar" });
    await userEvent.click(closeButtons[0]);

    const sidebar = screen.getByRole("complementary");
    expect(sidebar).not.toHaveClass("translate-x-0");
  });

  it("shows system healthy status indicator", () => {
    render(<Layout />);
    expect(screen.getByText("System Healthy")).toBeInTheDocument();
  });
});
