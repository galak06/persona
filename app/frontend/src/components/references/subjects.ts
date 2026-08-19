// Which of the brand's own subjects a reference photo is flagged as showing.
//
// A photo may show the mascot, the persona (the person behind the brand), both,
// or neither, and the wording has to be the same in both places it appears —
// the badge on the upload row the moment a photo is filed, and the badge on its
// tile in the library afterwards. Two spellings of the same fact read as two
// different facts.
//
// Its own module rather than an export from either component, because a file
// that exports both a component and a helper breaks React Fast Refresh.

export interface SubjectFlags {
  shows_mascot: boolean;
  shows_persona: boolean;
}

/** "the persona and the mascot" / "the persona" / "the mascot". */
export function subjectsShown(image: SubjectFlags): string {
  if (image.shows_mascot && image.shows_persona) return "the persona and the mascot";
  if (image.shows_persona) return "the persona";
  return "the mascot";
}
