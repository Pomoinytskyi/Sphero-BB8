import pygame
import time

def main():
    # Initialize all imported pygame modules
    pygame.init()
    pygame.joystick.init()

    # Check how many joysticks (controllers) are attached
    joystick_count = pygame.joystick.get_count()
    print(f"Number of joysticks: {joystick_count}")

    if joystick_count == 0:
        print("No joystick connected. Exiting.")
        return

    # Select the first joystick
    joystick = pygame.joystick.Joystick(0)
    joystick.init()
    print(f"Joystick name: {joystick.get_name()}")

    try:
        while True:
            # Pump and grab events
            pygame.event.pump()

            # Example: read axes
            axes = joystick.get_numaxes()
            axis_values = []
            for i in range(axes):
                axis_val = joystick.get_axis(i)
                axis_values.append(round(axis_val, 2))

            # Example: read buttons
            buttons = joystick.get_numbuttons()
            button_values = []
            for i in range(buttons):
                button_val = joystick.get_button(i)
                button_values.append(button_val)

            # Print them out (or handle them in your game logic)
            print(f"Axes: {axis_values}, Buttons: {button_values}")

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("Exiting...")

    # Clean up
    joystick.quit()
    pygame.joystick.quit()
    pygame.quit()

if __name__ == "__main__":
    main()