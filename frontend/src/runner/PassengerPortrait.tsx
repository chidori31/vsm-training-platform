import type { Portrait } from "./sceneContent";

export function PassengerPortrait({ kind = "passenger" }: { kind?: Portrait }) {
  return (
    <svg
      className="passenger-art"
      viewBox="0 0 480 440"
      aria-hidden="true"
      focusable="false"
    >
      <path fill="#d9e8e7" d="M0 0h480v440H0z" />
      <path fill="#f8f7ef" d="M0 0h480v38H0zM0 310h480v130H0z" />
      <path fill="#b9cfcd" d="m0 236 140-30 151 44 189-76v136H0z" />
      <g className="landscape-lines" fill="none" stroke="#fff" strokeWidth="4">
        <path d="M25 133h96m26 0h76M318 97h128M21 192h185M313 211h112" />
      </g>
      <path
        fill="none"
        stroke="#788f91"
        strokeWidth="2"
        d="M0 38h480M0 310h480M364 38v272"
      />
      <path fill="#243e48" d="M63 440V227q0-35 35-35h36q40 0 40 42v206z" />
      {kind === "pair" && (
        <g opacity=".8">
          <path
            fill="#7b9598"
            d="M362 304q13-36 46-36 43 0 59 54l13 118H338z"
          />
          <path
            fill="#c18665"
            d="M381 227q0-24 24-24 31 0 31 30v22q-2 28-29 28-26-4-26-34z"
          />
          <path
            fill="#394b4c"
            d="M376 230q-9-39 23-45 36-6 41 40l-21-8-5 12-11-10z"
          />
        </g>
      )}
      <path
        fill={kind === "colleague" ? "#27434b" : "#eee5d2"}
        d="m94 440 9-88q5-44 44-59l38-14h62l48 18q30 15 40 63l17 80z"
      />
      <path fill="#c7896b" d="m184 263-1 30 32 23 34-25-9-39z" />
      <path
        fill="#dda181"
        d="M170 160q10-42 48-41 55 0 55 60l-7 52q-3 47-44 50-35-2-48-48z"
      />
      <path
        fill="#263c3e"
        d="M168 198q-18-21-10-56 9-34 48-40 39-6 60 25 17 22 9 62l-14-2-8-40q-28 21-68 10l-5 41z"
      />
      <path fill="#dda181" d="M172 194q-19-12-17 9 2 22 23 23z" />
      <g stroke="#533d36" fill="none" strokeWidth="2.5" strokeLinecap="round">
        <path d="m192 192 10-2m33 0 10 2m-26 5-4 26 10 1m-20 19q15 6 25-2" />
      </g>
      <path
        fill={kind === "colleague" ? "#e9d64e" : "#1a343e"}
        d="m180 280 35 30 33-31 14 24-25 24-12 63-22-4-6-62-32-21z"
      />
      <path
        stroke="#b5afa1"
        fill="none"
        strokeWidth="2"
        d="m153 321-13 119m146-119 15 119"
      />
      <path fill="#dda181" d="m281 384-55-17q-14-2-17 8-2 9 13 14l44 19z" />
      <path
        fill={kind === "colleague" ? "#35515a" : "#dbd2be"}
        d="m315 357 13 51q-5 19-33 16l-40-13 13-37 25 11-7-25z"
      />
      <path fill="#102b35" d="M0 418h86v22H0z" />
      <path stroke="#102b35" strokeWidth="2" d="M0 439h480" />
    </svg>
  );
}
