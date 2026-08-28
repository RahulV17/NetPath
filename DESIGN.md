# NetPath Design System

## Product
NetPath is a networking data-path engineering portfolio project. The frontend is an interactive 3D lab that visualizes packets traveling through a router pipeline. Users are students, engineers, and hiring managers.

## Register
Product/tool UI with strong visual identity. Dark, technical, controlled. The design should feel like a mission-control surface for network traffic, not a generic SaaS dashboard.

## Memorable Thing
“You are watching packets move through real hardware stages in real time.”

## Aesthetic Direction
Dark industrial-luxe terminal. Near-black surfaces, warm brass accents, cyan/green telemetry, restrained glow. Think Bloomberg terminal meets spacecraft cockpit.

## Color Strategy
Restrained neon-on-dark with one warm metal accent.

- Background: `#05070a`
- Surface: `#0b0f14`
- Panel: `rgba(7,9,12,0.84)`
- Border: `#1c2530`, `#30363d`
- Ink primary: `#e8e0cc`
- Ink secondary: `#9d978a`
- Ink muted: `#484f58`
- Accent cyan: `#6fc7e8`
- Accent gold: `#b08d57`
- Accent green: `#39d353`
- Accent red: `#d45f49`
- Accent purple: `#bc8cff`
- Accend orange: `#ffa657`

## Typography
Display: `Playfair Display` for hero/title
Mono: `JetBrains Mono` for labels, telemetry, controls
Body: `Inter` for small helper text

Scale:
- Hero title: clamp(1.5rem, 2.5vw, 2.5rem)
- Section header: 0.65rem, 0.25em tracking, mono
- Label: 0.68rem
- Body: 0.78rem
- Micro: 0.62rem

## Motion
- Intentional only.
- Button hover: border/color transition 150ms ease-out.
- Active station pulse: sine-driven emissive, gated by reduced motion.
- No decorative motion.

## Layout
- 3D canvas fills viewport.
- Left controls: desktop-collapsible panel, reclaimed space when hidden.
- Right readout: desktop-collapsible panel.
- Mobile: bottom drawers.

## Anti-patterns
- No gradient text.
- No glassmorphism by default.
- No identical card grids.
- No eyebrow kickers above every section.
- No numbered scaffolding unless it is a real sequence.

## Frontend Target
Improve `web/src/lab/*`, `web/src/sim/*`, and `web/src/index.css` to use this token system consistently.
