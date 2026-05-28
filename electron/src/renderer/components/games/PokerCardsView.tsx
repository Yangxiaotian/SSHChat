import React from 'react';

type Props = {
  title: string;
  cards: string[];
};

function suitStyle(card: string): 'red' | 'black' {
  if (card.includes('红桃') || card.includes('方块') || card.includes('♥') || card.includes('♦')) return 'red';
  return 'black';
}

export default function PokerCardsView({ title, cards }: Props) {
  if (!cards.length) return null;
  return (
    <div className="poker-section">
      <div className="poker-title">{title}</div>
      <div className="poker-cards">
        {cards.map((card, idx) => (
          <div key={`${card}-${idx}`} className={`poker-card ${suitStyle(card)}`}>
            {card}
          </div>
        ))}
      </div>
    </div>
  );
}
