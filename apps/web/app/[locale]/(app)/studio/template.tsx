// template 每次导航重挂载 —— 复刻原 App.tsx 的切页 fade-up 动画。
export default function StudioTemplate({ children }: { children: React.ReactNode }) {
  return <div className="fade-up">{children}</div>;
}
