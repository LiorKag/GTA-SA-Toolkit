# Changelog

## 1.0.1

Changes asked for in the extensions.blender.org review:

- Nothing runs when a file opens or saves anymore. Files made with an older, pre-release version are
  updated with the Update Older File button in the add-on's preferences.
- Textures are packed into the .blend as they're imported (faster than before, too).
- The Studio part is a normal part of the add-on now. Its switches in the preferences show or hide its
  tabs and sections.
- The only tag is Import-Export.

## 1.0.0 (first public release)

- Map import (areas picked on the radar map with a preview, or whole sections) with textures, 2DFX
  lights and collisions; skip already placed objects; remove an import in one click.
- Peds (by name, ID, type, story characters), vehicles (wheels, colours, lights, paintjobs, doors, upgrades),
  weapons and objects, SA-MP objects and toys, custom / modded models from your own folders.
- IFP animations: browse, apply, combine (Animate tab), bake and export (ANP3 / ANPK).
- GTA SA Studio: time of day and weather from timecyc.dat, map light groups, camera and render helpers,
  toy editor, Pawn export, traffic and ped paths. Each part can be switched off in the preferences.
- Fix Missing GTA Textures for older .blend files whose textures point to a folder that's gone.
- Its own readers for DFF, TXD, COL, IMG, IPL, IDE, IFP and path nodes; no other add-on needed.
- Blender 4.4 and newer.
