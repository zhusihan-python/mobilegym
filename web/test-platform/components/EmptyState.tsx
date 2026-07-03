type EmptyStateProps = {
  title: string;
  body: string;
};

export function EmptyState({ title, body }: EmptyStateProps) {
  return (
    <section className="tp-empty">
      <h2>{title}</h2>
      <p>{body}</p>
    </section>
  );
}
