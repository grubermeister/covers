/**
 * @jest-environment jsdom
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { WrongImageKindWarning } from "./WrongImageKindWarning";

describe("WrongImageKindWarning (issue #76)", () => {
  it("renders nothing when no image looks wrong", () => {
    const { container } = render(
      <WrongImageKindWarning
        expected="MARKING"
        count={0}
        acknowledged={false}
        onAcknowledgedChange={jest.fn()}
      />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("tells a marking submitter their image looks like a cover", () => {
    render(
      <WrongImageKindWarning
        expected="MARKING"
        count={1}
        acknowledged={false}
        onAcknowledgedChange={jest.fn()}
      />,
    );
    expect(screen.getByText(/looks like a whole cover/i)).toBeTruthy();
  });

  it("reverses the message on the cover form", () => {
    render(
      <WrongImageKindWarning
        expected="COVER"
        count={1}
        acknowledged={false}
        onAcknowledgedChange={jest.fn()}
      />,
    );
    expect(screen.getByText(/looks like a marking close-up/i)).toBeTruthy();
  });

  it("always offers the override, so the contributor is never stuck", async () => {
    const onAcknowledgedChange = jest.fn();
    render(
      <WrongImageKindWarning
        expected="MARKING"
        count={1}
        acknowledged={false}
        onAcknowledgedChange={onAcknowledgedChange}
      />,
    );
    await userEvent.click(screen.getByRole("checkbox"));
    expect(onAcknowledgedChange).toHaveBeenCalledWith(true);
  });

  it("says how many images are affected when there is more than one", () => {
    render(
      <WrongImageKindWarning
        expected="MARKING"
        count={3}
        acknowledged={false}
        onAcknowledgedChange={jest.fn()}
      />,
    );
    expect(screen.getByText(/3 of the images/i)).toBeTruthy();
  });
});
