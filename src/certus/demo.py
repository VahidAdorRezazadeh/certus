"""Editable engineering requests for customer demonstrations.

Dimensions absent from the paper image are explicit demo design choices.
These requests use the normal interpretation, geometry and solver checks.
"""

L_BRACKET = """An L-shaped steel bracket. Use millimetres, with Z upwards. The base is 60 x 50 mm, 8 mm thick, centred on X=0, Y=0, with its bottom at Z=0. The upright wall spans the full 60 mm width in X, is 8 mm thick in Y, and rises 30 mm above the base. Put it at the +Y edge: Y=17 to 25 mm and Z=8 to 38 mm. Make one 10 mm diameter through-hole along Y through the wall, centred at X=0, Z=30 mm. Make a single solid, with square edges, no fillets, chamfers or other holes. Overall dimensions are 60 x 50 x 38 mm.

Use linear elastic steel, Young's modulus 210000 MPa and Poisson ratio 0.30. Yield is 250 MPa. A pin in the hole pulls 2 kN downwards (-Z); apply this as a total bearing force on the cylindrical hole surface, not pressure. The bottom face is bolted to the table: idealise the entire bottom face at Z=0 as fully fixed in all three translations. Do not model the pin, bolts or table.

Does it yield? Run a small-displacement, static linear elastic analysis. Compare von Mises stress with the stated yield strength and report maximum displacement and the checks supporting the result. If an image is attached, use it as a shape reference; the dimensions and analysis inputs written here define this demo. Ask about any conflicting features before generating geometry."""

PAPER_BRACKET = """A steel clevis bracket with two parallel rounded lugs, four base mounting holes, rounded base corners and lug-root fillets, as shown in the optional paper screenshot. Use millimetres, with Z upwards. The image labels a 63.4 x 50.7 mm base and a 30.5 mm lug height above the top of the base. For this demo, use the additional dimensions below as explicit design inputs; they are chosen values, not measurements inferred from the image. Model the same bracket with or without the image.

Base plate: 63.4 mm in X by 50.7 mm in Y, 2.54 mm thick, centred on X=0, Y=0, with bottom Z=0. Round the four plan-view corners to radius 8 mm. Add four 5 mm diameter through-holes along Z, with centres at X=+/-26.2 mm, Y=+/-19.85 mm (5.5 mm inset from the straight plate edges).

Lugs: two identical 5 mm thick walls in X, with a 12 mm clear gap; their X ranges are -11 to -6 mm and +6 to +11 mm. Each lug is 20 mm wide in Y, centred at Y=0. Its profile rises from Z=2.54 to Z=23.04 mm as a rectangle, topped by a semicircle of radius 10 mm centred at Y=0, Z=23.04 mm. Thus the lug rises 30.5 mm above the plate and the overall part height is 33.04 mm. Add one coaxial 8 mm diameter pin bore along X through both lugs, centred at Y=0, Z=23.04 mm. Add radius 3 mm fillets to the four straight lug-to-plate root edges parallel to Y, on both sides of each lug. Fuse the plate and both lugs into one solid. No countersinks, counterbores, chamfers, grooves or other features; do not interpret shaded outlines in the image as extra grooves. Overall dimensions are 63.4 x 50.7 x 33.04 mm.

Use linear elastic steel, Young's modulus 210000 MPa, Poisson ratio 0.30 and yield strength 250 MPa. A pin through the 8 mm bore pulls a total of 2 kN downwards (-Z), shared equally by the two lugs. Apply a bearing force to both cylindrical pin-bore surfaces, not to the four mounting holes and not as pressure. The complete bottom face of the plate at Z=0 is fully fixed in all three translations, idealising attachment to a rigid table. Do not model the pin, bolts or table.

Does it yield? Run a small-displacement, static linear elastic analysis. Compare von Mises stress with 250 MPa and report maximum displacement and the checks supporting the result. Include both lugs, all four mounting holes and the specified fillets. Use the image to confirm the clevis shape and use the written dimensions to complete the geometry. The chosen dimensions, material, loading and idealised support above are intentional demo inputs, even where the paper image does not specify them. Ask only if there is a remaining actual contradiction or missing input."""

DEFAULT_PROMPT = PAPER_BRACKET
