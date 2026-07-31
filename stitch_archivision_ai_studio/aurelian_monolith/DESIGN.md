---
name: Aurelian Monolith
colors:
  surface: '#131313'
  surface-dim: '#131313'
  surface-bright: '#3a3939'
  surface-container-lowest: '#0e0e0e'
  surface-container-low: '#1c1b1b'
  surface-container: '#201f1f'
  surface-container-high: '#2a2a2a'
  surface-container-highest: '#353534'
  on-surface: '#e5e2e1'
  on-surface-variant: '#d1c5b4'
  inverse-surface: '#e5e2e1'
  inverse-on-surface: '#313030'
  outline: '#9a8f80'
  outline-variant: '#4e4639'
  surface-tint: '#e9c176'
  primary: '#e9c176'
  on-primary: '#412d00'
  primary-container: '#c5a059'
  on-primary-container: '#4e3700'
  inverse-primary: '#775a19'
  secondary: '#c8c6c3'
  on-secondary: '#30312e'
  secondary-container: '#474744'
  on-secondary-container: '#b6b5b1'
  tertiary: '#c8c6c5'
  on-tertiary: '#313030'
  tertiary-container: '#a7a5a5'
  on-tertiary-container: '#3b3b3b'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#ffdea5'
  primary-fixed-dim: '#e9c176'
  on-primary-fixed: '#261900'
  on-primary-fixed-variant: '#5d4201'
  secondary-fixed: '#e4e2de'
  secondary-fixed-dim: '#c8c6c3'
  on-secondary-fixed: '#1b1c1a'
  on-secondary-fixed-variant: '#474744'
  tertiary-fixed: '#e5e2e1'
  tertiary-fixed-dim: '#c8c6c5'
  on-tertiary-fixed: '#1c1b1b'
  on-tertiary-fixed-variant: '#474746'
  background: '#131313'
  on-background: '#e5e2e1'
  surface-variant: '#353534'
typography:
  display-lg:
    fontFamily: Playfair Display
    fontSize: 64px
    fontWeight: '700'
    lineHeight: '1.1'
    letterSpacing: -0.02em
  display-lg-mobile:
    fontFamily: Playfair Display
    fontSize: 40px
    fontWeight: '700'
    lineHeight: '1.2'
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Playfair Display
    fontSize: 32px
    fontWeight: '600'
    lineHeight: '1.3'
  headline-sm:
    fontFamily: Playfair Display
    fontSize: 24px
    fontWeight: '500'
    lineHeight: '1.4'
  body-lg:
    fontFamily: IBM Plex Sans
    fontSize: 18px
    fontWeight: '400'
    lineHeight: '1.6'
  body-md:
    fontFamily: IBM Plex Sans
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.6'
  label-caps:
    fontFamily: IBM Plex Sans
    fontSize: 12px
    fontWeight: '600'
    lineHeight: '1.2'
    letterSpacing: 0.15em
  technical-data:
    fontFamily: IBM Plex Sans
    fontSize: 14px
    fontWeight: '500'
    lineHeight: '1.4'
spacing:
  unit: 8px
  container-max: 1440px
  gutter: 32px
  margin-desktop: 64px
  margin-mobile: 24px
  section-gap: 128px
---

## Brand & Style
The design system embodies the precision and exclusivity of a high-end architectural firm. The brand personality is authoritative yet understated, appealing to an elite clientele that values structural integrity and timeless aesthetics. 

The visual style is a blend of **Minimalism** and **High-Contrast**, utilizing a "Material-First" philosophy. It treats digital space like physical stone and metal—heavy, permanent, and meticulously crafted. Large expanses of whitespace (negative space) act as architectural voids, allowing the content to breathe and emphasizing the importance of every line and character. The emotional response should be one of quiet confidence, technical mastery, and uncompromising luxury.

## Colors
The palette is rooted in a "Midnight and Metal" theme. 

- **Primary (#c5a059):** A soft champagne gold used sparingly for critical accents, interactive states, and thin structural borders. It represents the "brass and light" of an architectural model.
- **Secondary (#fdfbf7):** Refined ivory used for high-contrast typography against dark backgrounds and for secondary surface cards to create a sense of layered paper or marble.
- **Neutral/Base (#0a0a0a):** Deep midnight charcoal serves as the infinite canvas, providing a sense of depth and weight.
- **Surface Tertiary (#1a1a1a):** A slightly lifted charcoal used for container backgrounds to provide subtle contrast against the base layer.

## Typography
The typographic hierarchy relies on the tension between the romanticism of the serif and the utility of the sans-serif.

- **Headlines:** Use **Playfair Display** for all major headings. It provides the "editorial" feel of a luxury monograph. Large display sizes should use tighter letter spacing to emphasize the stroke contrast.
- **Body & Technicals:** Use **IBM Plex Sans** for its engineered, architectural quality. It handles technical data and long-form descriptions with high legibility.
- **Labels:** Small labels and metadata must be in all-caps IBM Plex Sans with generous letter spacing to evoke the feeling of architectural blueprints.

## Layout & Spacing
The layout follows a **Fixed Grid** philosophy on desktop to maintain precise mathematical proportions. 

- **Grid:** A 12-column system with wide 32px gutters. Elements should often span 4, 6, or 8 columns to create asymmetrical, dynamic compositions.
- **Rhythm:** An 8px linear scale. Large vertical gaps (128px+) are encouraged between major sections to mimic the airy feel of a gallery.
- **Mobile:** On mobile, margins reduce to 24px, and the layout collapses to a single column, maintaining the gold 1px separators between vertical modules.

## Elevation & Depth
In this design system, depth is achieved through **Tonal Layers** and **Subtle Gold Outlines** rather than aggressive shadows.

- **Surfaces:** Level 0 is the Deep Charcoal (#0a0a0a) background. Level 1 is the Surface Tertiary (#1a1a1a).
- **Outlines:** All cards and containers use a 1px solid border. The border color is typically a low-opacity version of the Champagne Gold, appearing almost like a metallic wireframe.
- **Shadows:** Use extremely soft, large-radius shadows (e.g., 40px blur, 5% opacity) only for floating elements like modals. The shadow should have a slight gold/warm tint to prevent it from looking "muddy" on the dark background.

## Shapes
The shape language is strictly **Sharp (0px)**. 

To reflect the structural rigidity of modern architecture, all buttons, input fields, cards, and images must have 90-degree corners. This evokes a sense of custom-cut stone and precision-milled metal. Circular elements are permitted only for specialized icons or avatars, but they should be framed within a square container.

## Components
- **Buttons:** Primary buttons are Champagne Gold (#c5a059) with Black (#0a0a0a) text. Secondary buttons are transparent with a 1px Gold border. All buttons use the `label-caps` typography style.
- **Input Fields:** Bottom-border only (1px Ivory) by default. Upon focus, the border transitions to Gold. Labels should sit above the field in small-caps.
- **Cards:** No background fill by default; use a 1px Gold border with 10% opacity. For "Premium" cards, use the Refined Ivory (#fdfbf7) background with Midnight (#0a0a0a) text.
- **Lists:** Separated by horizontal 1px lines that span the full width of the container, mimicking a ledger or technical drawing.
- **Chips:** Rectangular (0px radius) with a 1px Gold border and technical-data typography.
- **Navigation:** Top-tier navigation should be minimalist, using high-tracked IBM Plex Sans. Active states are indicated by a thin gold line *above* the text.