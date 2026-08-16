import type { CharacterProfile } from "../../api/types";

interface CharacterPanelProps {
  profiles: CharacterProfile[];
}

/**
 * The cast is what keeps six separate generations looking like one story, so it is shown
 * rather than hidden inside the prompts.
 */
export function CharacterPanel({ profiles }: CharacterPanelProps) {
  if (!profiles.length) {
    return null;
  }

  return (
    <section className="character-panel" aria-labelledby="character-panel-title">
      <div className="character-panel__heading">
        <p className="eyebrow">RECURRING CAST</p>
        <h3 id="character-panel-title">Characters in every cut</h3>
      </div>
      <ul className="character-list">
        {profiles.map((profile) => (
          <li className="character-card" key={profile.name}>
            <h4>{profile.name}</h4>
            <p className="character-card__role">{profile.role}, age {profile.ageRange}</p>
            <dl>
              <div><dt>Hair</dt><dd>{profile.hairColor} {profile.hairStyle}</dd></div>
              <div><dt>Outfit</dt><dd>{profile.outfit}</dd></div>
              <div><dt>Build</dt><dd>{profile.build}</dd></div>
              <div><dt>Face</dt><dd>{profile.faceImpression}</dd></div>
              <div><dt>Carries</dt><dd>{profile.signatureProp}</dd></div>
            </dl>
          </li>
        ))}
      </ul>
    </section>
  );
}
