"""
Parametric CadQuery reconstruction of radiator_flow_assembly.stl  (units: mm)

Dimensions were measured from the STL (cross-sections + planar-face analysis).
Local frame: min corner of the STL bounding box = (0, 0, 0)
    X = across the core (tube pitch direction), 260 mm
    Y = core depth / air direction,              50 mm
    Z = tube length / coolant direction,        309 mm

Bodies (kept as separate solids, like the source STL -- NOT fused):
    tanks    : bottom + top hollow tanks, 2 mm wall, with header plates
    tubes    : 25 flat tubes, 0.2 mm wall, bore open into both tanks
    fins     : 24 banks x 165 plain fins, 0.5 mm thick, 1.5 mm pitch
    duct     : optional air-side envelope box around the core
    ports    : circular coolant ports (bottom-left wall, top face)

Run:  python radiator_flow_assembly.py   -> writes STEP + STL next to the script
Needs: pip install cadquery
"""
import cadquery as cq

# ----------------------------------------------------------------- parameters
W, D, H        = 260.0, 50.0, 309.0     # overall envelope
Y_C            = 27.01                  # tank / tube centreline in Y

# tanks
TANK_D         = 30.0                   # tank depth (Y), centred on Y_C
TANK_WALL      = 2.0
BOT_Z0, BOT_Z1 = 0.0, 27.0              # bottom tank outer z-range (plate = 25..27)
TOP_Z0, TOP_Z1 = 277.0, 309.0           # top tank outer z-range   (plate = 277..279)

# tubes
N_TUBES        = 25
TUBE_X0        = 5.0                    # left outer face of first tube
TUBE_PITCH     = 10.0
TUBE_W         = 2.0                    # outer width  (X)
TUBE_DEPTH     = 15.0                   # outer depth  (Y), 19.51 .. 34.51
TUBE_T         = 0.2                    # wall thickness
TUBE_Z0, TUBE_Z1 = 25.0, 279.0          # tube ends sit inside the tank cavities

# fins (one bank sits between each pair of tubes)
FIN_T          = 0.5
FIN_PITCH      = 1.5
FIN_Z0         = 29.35                  # bottom face of first fin
N_FINS         = 165
FIN_Y0, FIN_Y1 = 20.38, 33.94           # fin depth in Y (slightly inside the tube)

# ports (each ends in a 0.1 mm boundary disc in the STL; omitted here)
PORT_TOP       = dict(d=18.63, x=130.0,  y=27.01)          # through top wall, +Z
PORT_BOT       = dict(d=7.34,  y=27.62, z=13.02)           # through left wall, -X

INCLUDE_DUCT   = True                   # air-side box: x 5..260, y 0..50, z 27..282
ORIGIN         = (0.0, 0.0, 0.0)        # set to (620.607, 925.524, 1242.638) to
                                        # reproduce the original vehicle coordinates


# ----------------------------------------------------------------- helpers
def box(x0, x1, y0, y1, z0, z1):
    return (cq.Workplane("XY")
            .box(x1 - x0, y1 - y0, z1 - z0, centered=False)
            .translate((x0, y0, z0)))


def hollow_tank(z0, z1, plate_at_top):
    """Rectangular tank, 2 mm walls; the header plate is the face toward the core."""
    y0, y1 = Y_C - TANK_D / 2, Y_C + TANK_D / 2
    outer = box(0, W, y0, y1, z0, z1)
    t = TANK_WALL
    if plate_at_top:     # bottom tank: plate on its +Z face (z 25..27)
        cav = box(t, W - t, y0 + t, y1 - t, z0 + t, z1 - t)
    else:                # top tank: plate on its -Z face (z 277..279)
        cav = box(t, W - t, y0 + t, y1 - t, z0 + t, z1 - t)
    return outer.cut(cav)


def tube_xs():
    return [TUBE_X0 + i * TUBE_PITCH for i in range(N_TUBES)]


# ----------------------------------------------------------------- build
def build():
    # --- tanks
    bot = hollow_tank(BOT_Z0, BOT_Z1, True)
    top = hollow_tank(TOP_Z0, TOP_Z1, False)

    # --- coolant bore slots through the header plates (so tube bores open into tanks)
    yi0 = Y_C - TUBE_DEPTH / 2 + TUBE_T
    yi1 = Y_C + TUBE_DEPTH / 2 - TUBE_T
    for x in tube_xs():
        slot_b = box(x + TUBE_T, x + TUBE_W - TUBE_T, yi0, yi1, 25.0, 27.0)
        slot_t = box(x + TUBE_T, x + TUBE_W - TUBE_T, yi0, yi1, 277.0, 279.0)
        bot = bot.cut(slot_b)
        top = top.cut(slot_t)

    # --- ports
    pt = PORT_TOP
    top = top.cut(cq.Workplane("XY").workplane(offset=TOP_Z1 - TANK_WALL)
                  .center(pt["x"], pt["y"]).circle(pt["d"] / 2).extrude(TANK_WALL))
    pb = PORT_BOT
    bot = bot.cut(cq.Workplane("YZ").center(pb["y"], pb["z"])
                  .circle(pb["d"] / 2).extrude(TANK_WALL))

    # --- one hollow flat tube, replicated along X
    ytop = Y_C - TUBE_DEPTH / 2
    tube = box(0, TUBE_W, ytop, ytop + TUBE_DEPTH, TUBE_Z0, TUBE_Z1).cut(
        box(TUBE_T, TUBE_W - TUBE_T, ytop + TUBE_T, ytop + TUBE_DEPTH - TUBE_T,
            TUBE_Z0 - 1, TUBE_Z1 + 1))
    tube_solid = tube.val()
    tubes = [tube_solid.moved(cq.Location(cq.Vector(x, 0, 0))) for x in tube_xs()]

    # --- one fin bank (165 plates) between two tubes, replicated along X
    gap = TUBE_PITCH - TUBE_W                       # 8 mm clear between tubes
    fin = cq.Workplane("XY").box(gap, FIN_Y1 - FIN_Y0, FIN_T, centered=False).val()
    bank = cq.Compound.makeCompound([
        fin.moved(cq.Location(cq.Vector(0, FIN_Y0, FIN_Z0 + k * FIN_PITCH)))
        for k in range(N_FINS)
    ])
    banks = [bank.moved(cq.Location(cq.Vector(x + TUBE_W, 0, 0)))
             for x in tube_xs()[:-1]]

    # --- assemble (separate solids, as in the source mesh)
    asm = cq.Assembly(name="radiator_flow_assembly")
    asm.add(bot.val(),  name="tank_bottom", color=cq.Color(0.6, 0.6, 0.65, 1))
    asm.add(top.val(),  name="tank_top",    color=cq.Color(0.6, 0.6, 0.65, 1))
    asm.add(cq.Compound.makeCompound(tubes),                  name="tubes",
            color=cq.Color(0.8, 0.5, 0.2, 1))
    asm.add(cq.Compound.makeCompound(banks),                  name="fins",
            color=cq.Color(0.85, 0.85, 0.9, 1))
    if INCLUDE_DUCT:
        asm.add(box(5.0, W, 0.0, D, 27.0, 282.0).val(),       name="air_duct",
                color=cq.Color(0.2, 0.5, 1.0, 0.15))
    return asm


def as_compound(asm):
    """Flatten the assembly to one compound (for STL export / measurement)."""
    return asm.toCompound()


if __name__ == "__main__":
    asm = build()
    comp = as_compound(asm)
    if any(ORIGIN):
        comp = comp.moved(cq.Location(cq.Vector(*ORIGIN)))
    bb = comp.BoundingBox()
    print(f"bbox  x {bb.xmin:.2f}..{bb.xmax:.2f}  y {bb.ymin:.2f}..{bb.ymax:.2f}  "
          f"z {bb.zmin:.2f}..{bb.zmax:.2f}")
    asm.export("radiator_flow_assembly.step")
    cq.exporters.export(cq.Workplane("XY").add(comp), "radiator_flow_assembly.stl",
                        tolerance=0.01, angularTolerance=0.1)
    print("wrote radiator_flow_assembly.step / .stl")