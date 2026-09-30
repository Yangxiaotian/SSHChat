import React from 'react';

type Props = {
  title: string;
  cards: string[];
  /** 闷牌时显示若干张牌背（炸金花）。 */
  faceDown?: number;
};

function suitStyle(card: string): 'red' | 'black' {
  if (card.includes('红桃') || card.includes('方块') || card.includes('♥') || card.includes('♦')) return 'red';
  return 'black';
}

export default function PokerCardsView({ title, cards, faceDown = 0 }: Props) {
  if (!cards.length && faceDown <= 0) return null;
  return (
    <div className="poker-section">
      <div className="poker-title">{title}</div>
      <div className="poker-cards">
        {faceDown > 0
          ? Array.from({ length: faceDown }, (_, idx) => (
              <div key={`back-${idx}`} className="poker-card poker-card-back" aria-hidden>
                🂠
              </div>
            ))
          : cards.map((card, idx) => (
              <div key={`${card}-${idx}`} className={`poker-card ${suitStyle(card)}`}>
                {card}
              </div>
            ))}
      </div>
    </div>
  );
}
