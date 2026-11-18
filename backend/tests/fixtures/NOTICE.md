# Test fixtures

* `astronaut.jpg` — portrait of NASA astronaut Eileen Collins, downscaled from the
  `astronaut.png` sample bundled with scikit-image 0.26.0 (`skimage/data`). The
  scikit-image documentation states: "This image was downloaded from the NASA Great
  Images database. No known copyright restrictions, released into the public domain."
  Used only to give face detection/reframing tests a real human face.
* Synthetic test videos are generated at test time by `make_test_media.py` using
  FFmpeg's built-in `flite` text-to-speech and drawing filters. No third-party
  video content is used.
