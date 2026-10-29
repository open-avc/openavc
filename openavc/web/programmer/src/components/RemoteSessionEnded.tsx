interface RemoteSessionEndedProps {
  /** There were unsaved edits in the tab when the session ended. */
  unsavedWork?: boolean;
}

/** What a tab opened through Remote Access shows once that session is gone. */
export function RemoteSessionEnded({ unsavedWork }: RemoteSessionEndedProps) {
  return (
    <div
      style={{
        height: "100vh",
        width: "100vw",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg-primary, #1a1a2e)",
        color: "var(--text-primary, #fff)",
      }}
    >
      <div
        role="status"
        style={{
          width: 360,
          padding: 32,
          borderRadius: 8,
          background: "var(--bg-secondary, #232342)",
          boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
          textAlign: "center",
        }}
      >
        <h2 style={{ margin: 0, fontSize: 20 }}>OpenAVC Programmer</h2>
        <p style={{ marginTop: 12, marginBottom: 0, fontSize: 14 }}>
          This remote session has ended. Open it again from the portal.
        </p>
        {unsavedWork && (
          <p style={{ marginTop: 8, marginBottom: 0, fontSize: 13, opacity: 0.8 }}>
            Your most recent changes were not saved.
          </p>
        )}
      </div>
    </div>
  );
}
