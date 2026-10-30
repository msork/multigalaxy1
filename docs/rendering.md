# Rendering

Independent player views require independent camera transforms and projection state, viewport/scissor management, repeated scene rendering, and HUD decisions. The original renderer may keep global camera, GX state, visibility, and render-pass data; each must be investigated before adding extra passes.

Target layouts: one full viewport; two horizontal or vertical; three independently framed; four quadrants. Online client-specific full-screen camera is a local presentation mode; local split screen remains supported. Multi-pass rendering increases scene cost and may multiply expensive render work. Measure on Dolphin and inspect framebuffer/XFB constraints before selecting implementation.

No viewport support is implemented yet. Camera APIs and addresses remain unknown until confirmed in Petari/disassembly for the selected revision.
