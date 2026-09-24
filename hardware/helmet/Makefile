# Regenerate the STLs and preview renders from the OpenSCAD sources.
# Needs openscad (2021.01 or newer) and, for the PNGs, xvfb-run on a headless box.
OPENSCAD ?= openscad
XVFB     ?= $(shell command -v xvfb-run >/dev/null 2>&1 && echo "xvfb-run -a")
RENDER    = $(XVFB) $(OPENSCAD) --imgsize=1000,1000 --projection=p --colorscheme=Tomorrow

STL = stl/bb8_pilot_helmet.stl stl/bb8_goggle_helmet.stl stl/goggle_lens.stl stl/bb8_goggles_up_helmet.stl \
      stl/fit_rings.stl stl/head_reference.stl
PNG = preview/assembly_front.png preview/assembly_rear.png preview/assembly_side.png \
      preview/assembly_top.png preview/helmet_alone.png preview/fit_rings.png \
      preview/goggle_front.png preview/goggle_straight.png preview/goggle_side.png preview/goggle_lens.png \
      preview/goggles_up_front.png preview/goggles_up_side.png preview/goggles_up_rear.png

all: $(STL) $(PNG)
stl: $(STL)
preview: $(PNG)

stl/%.stl: %.scad bb8_dimensions.scad helmet_common.scad
	@mkdir -p stl
	$(OPENSCAD) --export-format binstl -o $@ $<

preview/assembly_front.png: assembly.scad bb8_pilot_helmet.scad helmet_common.scad bb8_dimensions.scad
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
GOGGLE_DEPS = assembly_goggle.scad bb8_goggle_helmet.scad helmet_common.scad bb8_dimensions.scad
preview/goggle_front.png: $(GOGGLE_DEPS)
	$(RENDER) --camera=210,-130,95,0,0,-5 -o $@ $<
preview/goggle_straight.png: $(GOGGLE_DEPS)
	$(RENDER) --camera=260,0,30,0,0,5 -o $@ $<
preview/goggle_side.png: $(GOGGLE_DEPS)
	$(RENDER) --camera=20,-250,60,0,0,-5 -o $@ $<
preview/goggle_lens.png: goggle_lens.scad bb8_goggle_helmet.scad helmet_common.scad
	$(RENDER) --camera=60,-60,60,0,0,0 -o $@ $<
GUP_DEPS = assembly_goggles_up.scad bb8_goggles_up_helmet.scad helmet_common.scad bb8_dimensions.scad
preview/goggles_up_front.png: $(GUP_DEPS)
	$(RENDER) --camera=210,-130,95,0,0,-5 -o $@ $<
preview/goggles_up_side.png: $(GUP_DEPS)
	$(RENDER) --camera=20,-250,60,0,0,-5 -o $@ $<
preview/goggles_up_rear.png: $(GUP_DEPS)
	$(RENDER) --camera=-200,140,95,0,0,-5 -o $@ $<
preview/fit_rings.png: fit_rings.scad
	$(RENDER) --camera=56,-150,200,56,28,0 -o $@ $<

clean:
	rm -rf stl preview

.PHONY: all stl preview clean
stl/goggle_lens.stl: bb8_goggle_helmet.scad
stl/bb8_goggle_helmet.stl stl/goggle_lens.stl: helmet_common.scad
