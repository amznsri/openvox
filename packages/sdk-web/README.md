# @openvox/web

Embed an OpenVox voice agent in any React app with one component.

```bash
npm install @openvox/web
```

```tsx
import { VoiceAgent } from "@openvox/web";

export default function App() {
  return (
    <VoiceAgent
      server="http://localhost:3001"   // your OpenVox gateway
      agentId="069bfeea-dec3-414f-980d-6ac98adc632d"
    />
  );
}
```

Three lines, working voice agent. The component handles mic capture
(downsamples to 16 kHz PCM), playback (sentence-by-sentence audio
chunks), WS framing, and barge-in.

## Custom UIs

Use the hook directly if you want to render your own transcript /
debug pane / waveform:

```tsx
import { useVoiceSession } from "@openvox/web";

function CustomCall() {
  const { status, transcript, start, stop, interrupt } = useVoiceSession({
    server: "http://localhost:3001",
    agentId: "...",
    onEvent: (ev) => console.log(ev),  // see every server frame
  });

  return (
    <>
      <button onClick={start}>Start</button>
      <button onClick={stop}>Stop</button>
      <pre>{JSON.stringify(transcript, null, 2)}</pre>
    </>
  );
}
```

## Building

```bash
cd packages/sdk-web
pnpm install
pnpm build
```

Outputs `dist/index.js` (ESM), `dist/index.cjs` (CJS), and `dist/index.d.ts`.

## Protocol

The SDK speaks the same `/ws/voice` protocol as the dashboard. See
`docs/architecture.md §2` for the full sequence diagram.

## License

Apache 2.0 — same as the rest of OpenVox.
