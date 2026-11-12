-- Castle kit M1.0, the scale pass: the castle grew (data/world/layout.json:
-- 9 m storeys, bigger halls, corridors and common rooms, a new Grand
-- Staircase), so a position saved in the castle before it may now stand on
-- a different floor or inside a wall. The server already falls back to a
-- spawn when a saved position is off the ground; this also catches the ones
-- that land on a wrong but valid floor. Characters saved in the castle
-- resume at the layout's spawn (pos NULL). Dorm positions are in room
-- coordinates and the dorms did not change, so they stay.
UPDATE characters
SET pos_x = NULL, pos_y = NULL, pos_z = NULL
WHERE zone = 'castle' AND pos_x IS NOT NULL;
