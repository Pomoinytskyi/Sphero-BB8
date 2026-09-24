# Regenerate the STLs and preview renders from the OpenSCAD sources.
# Needs openscad (2021.01 or newer) and, for the PNGs, xvfb-run on a headless box.
OPENSCAD ?= openscad
XVFB     ?= $(shell command -v xvfb-run >/dev/null 2>&1 && echo "xvfb-run -a")
RENDER    = $(XVFB) $(OPENSCAD) --imgsize=1000,1000 --projection=p --colorscheme=Tomorrow

STL = stl/bb8_pilot_helmet.stl stl/fit_rings.stl stl/head_reference.stl
PNG = preview/assembly_front.png preview/assembly_rear.png preview/assembly_side.png \
      preview/assembly_top.png preview/helmet_alone.png preview/fit_rings.png

all: $(STL) $(PNG)
stl: $(STL)
preview: $(PNG)

stl/%.stl: %.scad bb8_dimensions.scad
	@mkdir -p stl
	$(OPENSCAD) --export-format binstl -o $@ $<

preview/assembly_front.png: assembly.scad bb8_pilot_helmet.scad bb8_dimensions.scad
	@mkdir -p preview
	$(RENDER) --camera=210,-130,95,0,0,-5 -o $@ $<
preview/assembly_rear.png: assembly.scad bb8_pilot_helmet.scad bb8_dimensions.scad
	$(RENDER) --camera=-200,140,95,0,0,-5 -o $@ $<
preview/assembly_side.png: assembly.scad bb8_pilot_helmet.scad bb8_dimensions.scad
	$(RENDER) --camera=20,-250,60,0,0,-5 -o $@ $<
preview/assembly_top.png: assembly.scad bb8_pilot_helmet.scad bb8_dimensions.scad
	$(RENDER) --camera=0.01,-40,250,0,0,-5 -o $@ $<
preview/helmet_alone.png: bb8_pilot_helmet.scad bb8_dimensions.scad
	$(RENDER) --camera=150,-110,80,0,0,8 -o $@ $<
preview/fit_rings.png: fit_rings.scad
	$(RENDER) --camera=56,-150,200,56,28,0 -o $@ $<

clean:
	rm -rf stl preview

.PHONY: all stl preview clean
