# A short terminal demo. The renderer sets Output, Width, Height and Framerate,
# and the theme's terminal colours (a `Set Theme` line here would override them).
Set Shell bash
Set Padding 40
Set TypingSpeed 60ms

Type "echo 'Hello from a terminal recording'"
Enter
Sleep 600ms
Type "python3 -c 'print(sum(range(1, 11)))'"
Enter
Sleep 1.2s
