import { describe, expect, it } from "vitest";
import { charCount, emptyDraft, enclosureText, hasEnclosures, letterBody, letterCost, letterProblem, MAIL_KIND_LABELS, MailIndicator, mailDate, mailError, sentEnclosureText, sentStatus } from "./mail-state";

const draft = (over = {}) => ({ ...emptyDraft(), to: "Basil", subject: "Hello", ...over });

describe("the Outbox", () => {
  const sent = (over = {}) => ({ read: false, collected: true, crowns: 0, attachments: 0, ...over });

  it("says where a plain letter stands", () => {
    expect(sentStatus(sent())).toBe("Delivered");
    expect(sentStatus(sent({ read: true }))).toBe("Read");
  });

  it("follows a letter that carried something until it is collected", () => {
    expect(sentStatus(sent({ collected: false, attachments: 2 }))).toBe("Delivered");
    expect(sentStatus(sent({ collected: false, read: true, crowns: 5 }))).toBe("Read, not collected");
    expect(sentStatus(sent({ collected: true, read: true, crowns: 5 }))).toBe("Collected");
  });

  it("names what went with a letter, collected or not", () => {
    expect(sentEnclosureText({ crowns: 0, attachments: 0 })).toBe("");
    expect(sentEnclosureText({ crowns: 15, attachments: 2 })).toBe("2 items + 15 C");
    expect(sentEnclosureText({ crowns: 0, attachments: 1 })).toBe("1 item");
  });
});

describe("letters", () => {
  it("needs a recipient other than yourself and a subject", () => {
    expect(letterProblem(draft({ to: " " }), "Ottilie", 10)).toMatch(/Who/);
    expect(letterProblem(draft({ to: "ottilie" }), "Ottilie", 10)).toMatch(/yourself/);
    expect(letterProblem(draft({ subject: "  " }), "Ottilie", 10)).toMatch(/subject/);
    expect(letterProblem(draft(), "Ottilie", 10)).toBeNull();
  });

  it("counts characters, not UTF-16 units", () => {
    expect(charCount("owl")).toBe(3);
    expect(charCount("🦉🦉")).toBe(2);
    expect(letterProblem(draft({ subject: "🦉".repeat(80) }), "O", 10)).toBeNull();
    expect(letterProblem(draft({ subject: "a".repeat(81) }), "O", 10)).toMatch(/80/);
    expect(letterProblem(draft({ body: "a".repeat(2001) }), "O", 10)).toMatch(/2,000/);
  });

  it("limits what an owl carries", () => {
    const items = Array.from({ length: 7 }, (_, i) => ({ itemId: i + 1, quantity: 1 }));
    expect(letterProblem(draft({ items }), "O", 10)).toMatch(/6 items/);
    expect(letterProblem(draft({ items: [{ itemId: 1, quantity: 0 }] }), "O", 10)).toMatch(/at least one/);
    expect(letterProblem(draft({ crowns: 100_001 }), "O", 1_000_000)).toMatch(/100,000/);
    expect(letterProblem(draft({ crowns: 2.5 }), "O", 10)).toMatch(/whole/);
  });

  it("needs the Crowns and the postage in the purse", () => {
    expect(letterCost({ crowns: 0 })).toBe(1);
    expect(letterCost({ crowns: 20 })).toBe(21);
    expect(letterProblem(draft({ crowns: 9 }), "O", 10)).toBeNull();
    expect(letterProblem(draft({ crowns: 10 }), "O", 10)).toMatch(/postage/);
    expect(letterProblem(draft(), "O", 0)).toMatch(/postage/);
  });

  it("omits an empty body", () => {
    expect(letterBody(draft({ body: "  " }))).toBeUndefined();
    expect(letterBody(draft({ body: "Hi\n" }))).toBe("Hi\n");
  });
});

describe("the mailbox", () => {
  it("describes what is enclosed", () => {
    expect(enclosureText({ collected: false, crowns: 0, attachments: 0 })).toBe("");
    expect(enclosureText({ collected: false, crowns: 1500, attachments: 2 })).toBe("2 items + 1,500 C");
    expect(enclosureText({ collected: true, crowns: 5, attachments: 0 })).toBe("Collected");
    expect(hasEnclosures({ collected: false, crowns: 0, attachments: 1 })).toBe(true);
    expect(hasEnclosures({ collected: true, crowns: 9, attachments: 1 })).toBe(false);
  });

  it("dates letters relative to today", () => {
    const now = new Date(2026, 9, 24, 18, 0);
    expect(mailDate(new Date(2026, 9, 24, 14, 5).toISOString(), now)).toMatch(/14:05/);
    expect(mailDate(new Date(2026, 9, 23, 9, 0).toISOString(), now)).toBe("Yesterday");
    expect(mailDate(new Date(2026, 9, 3).toISOString(), now)).toMatch(/3 Oct/);
    expect(mailDate("nonsense", now)).toBe("");
  });

  it("flies an owl only when the unread count goes up after sign-in", () => {
    const m = new MailIndicator();
    expect(m.notice(3)).toBe(false); // reported at sign-in
    expect(m.badge()).toBe("3");
    expect(m.notice(4)).toBe(true);
    expect(m.notice(2)).toBe(false); // read two
    m.reconnected();
    expect(m.notice(5)).toBe(false); // reported again on a new connection
    expect(m.notice(150)).toBe(true);
    expect(m.badge()).toBe("99+");
    m.notice(0);
    expect(m.badge()).toBe("");
  });

  it("explains refusals", () => {
    expect(mailError("uncollected", "")).toMatch(/Collect/);
    expect(mailError("something_new", "Server text")).toBe("Server text");
  });
});

describe("mail kind labels", () => {
  it("names every kind the server sends, the House Cup reward included", () => {
    for (const k of ["letter", "shop", "exchange_sold", "exchange_bought", "exchange_returned", "exchange_expired", "system", "house_cup"] as const) {
      expect(MAIL_KIND_LABELS[k]).toBeTruthy();
    }
    expect(MAIL_KIND_LABELS.house_cup).toBe("House Cup");
  });
});
