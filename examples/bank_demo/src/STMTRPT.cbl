       IDENTIFICATION DIVISION.
       PROGRAM-ID. STMTRPT.
      *================================================================*
      * STMTRPT - CUSTOMER STATEMENT REPORT                            *
      * INPUT (STDIN): 'H' HEADER (ONE PER STATEMENT) FOLLOWED BY 'T'  *
      * TRANSACTIONS. 10 DETAIL LINES PER PAGE.                        *
      * FORMATTING IS DONE BY FMTUTIL; DAY NAMES COME FROM DATEUTIL.   *
      *================================================================*
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT IN-FILE ASSIGN TO KEYBOARD
               ORGANIZATION IS LINE SEQUENTIAL.
       DATA DIVISION.
       FILE SECTION.
       FD  IN-FILE.
       01  IN-HEADER.
           05 IH-REC-TYPE          PIC X.
              88 IH-IS-HEADER      VALUE 'H'.
           05 IH-ACCT-ID           PIC X(8).
           05 IH-NAME              PIC X(20).
           05 IH-STMT-DATE         PIC 9(8).
           05 IH-OPEN-BAL          PIC S9(9)V99
                                   SIGN LEADING SEPARATE.
       01  IN-TXN.
           05 IT-REC-TYPE          PIC X.
              88 IT-IS-TXN         VALUE 'T'.
           05 IT-DATE              PIC 9(8).
           05 IT-CODE              PIC X.
              88 IT-CODE-VALUES    VALUE 'D' 'W' 'F' 'I'.
           05 IT-AMOUNT            PIC 9(7)V99.
           05 IT-DESC              PIC X(20).
       WORKING-STORAGE SECTION.
       COPY DATEPARM.
       COPY FMTPARM.
       01  WS-EOF                  PIC X VALUE 'N'.
       01  WS-IN-STMT              PIC X VALUE 'N'.
       01  WS-BAL                  PIC S9(9)V99 COMP-3.
       01  WS-SIGNED               PIC S9(9)V99 COMP-3.
       01  WS-PAGE                 PIC 9(3).
       01  WS-LINES                PIC 99.
       01  WS-DEBITS               PIC 9(4).
       01  WS-CREDITS              PIC 9(4).
       01  WS-DATE-X               PIC X(8).
       01  WS-DAY-NAMES            PIC X(21)
                                   VALUE 'MONTUEWEDTHUFRISATSUN'.
       01  WS-DAY-TABLE REDEFINES WS-DAY-NAMES.
           05 WS-DAY-NAME          PIC X(3) OCCURS 7 TIMES.
       01  OUT-PAGE-HDR.
           05 FILLER               PIC X(14) VALUE 'STATEMENT FOR '.
           05 OP-ID                PIC X(8).
           05 FILLER               PIC X VALUE SPACE.
           05 OP-NAME              PIC X(20).
           05 FILLER               PIC X(6) VALUE ' PAGE '.
           05 OP-PAGE              PIC ZZ9.
       01  OUT-DATE-LINE.
           05 FILLER               PIC X(15) VALUE 'STATEMENT DATE '.
           05 OD-DATE              PIC X(20).
       01  OUT-OPEN-LINE.
           05 FILLER               PIC X(16) VALUE 'OPENING BALANCE '.
           05 OO-BAL               PIC X(20).
       01  OUT-COLUMNS             PIC X(60) VALUE
           'DATE  DAY DESCRIPTION          AMOUNT            BALANCE'.
       01  OUT-DETAIL.
           05 ODT-MM               PIC XX.
           05 FILLER               PIC X VALUE '/'.
           05 ODT-DD               PIC XX.
           05 FILLER               PIC X VALUE SPACE.
           05 ODT-DAY              PIC X(3).
           05 FILLER               PIC X VALUE SPACE.
           05 ODT-DESC             PIC X(20).
           05 FILLER               PIC X VALUE SPACE.
           05 ODT-AMT              PIC X(17).
           05 ODT-BAL              PIC -ZZZ,ZZZ,ZZ9.99.
       01  OUT-BAD-TXN.
           05 FILLER               PIC X(18) VALUE 'INVALID DATE      '.
           05 OBT-DATE             PIC X(8).
       01  OUT-CLOSE-LINE.
           05 FILLER               PIC X(16) VALUE 'CLOSING BALANCE '.
           05 OC-BAL               PIC X(20).
           05 FILLER               PIC X(8) VALUE ' DEBITS='.
           05 OC-DEBITS            PIC ZZZ9.
           05 FILLER               PIC X(9) VALUE ' CREDITS='.
           05 OC-CREDITS           PIC ZZZ9.
       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN INPUT IN-FILE
           PERFORM UNTIL WS-EOF = 'Y'
               READ IN-FILE
                   AT END
                       MOVE 'Y' TO WS-EOF
                   NOT AT END
                       PERFORM 1000-PROCESS
               END-READ
           END-PERFORM
           CLOSE IN-FILE
           IF WS-IN-STMT = 'Y'
               PERFORM 4000-CLOSE-STATEMENT
           END-IF
           STOP RUN.

       1000-PROCESS.
           EVALUATE TRUE
               WHEN IH-IS-HEADER
                   IF WS-IN-STMT = 'Y'
                       PERFORM 4000-CLOSE-STATEMENT
                   END-IF
                   PERFORM 2000-OPEN-STATEMENT
               WHEN IT-IS-TXN
                   IF WS-IN-STMT = 'N'
                       DISPLAY 'NO HEADER - TRANSACTION SKIPPED'
                   ELSE
                       PERFORM 3000-DETAIL
                   END-IF
               WHEN OTHER
                   DISPLAY 'UNKNOWN RECORD SKIPPED'
           END-EVALUATE.

       2000-OPEN-STATEMENT.
           MOVE 'Y' TO WS-IN-STMT
           IF IH-OPEN-BAL IS NUMERIC
               MOVE IH-OPEN-BAL TO WS-BAL
           ELSE
               MOVE 0 TO WS-BAL
           END-IF
           MOVE 1 TO WS-PAGE
           MOVE 0 TO WS-LINES WS-DEBITS WS-CREDITS
           MOVE IH-ACCT-ID TO OP-ID
           MOVE IH-NAME TO OP-NAME
           PERFORM 2100-PAGE-HEADER
           MOVE 'A' TO FM-STYLE
           MOVE WS-BAL TO FM-AMOUNT
           CALL 'FMTUTIL' USING FMT-PARMS
           MOVE FM-OUT TO OO-BAL
           MOVE 'D' TO FM-STYLE
           IF IH-STMT-DATE IS NUMERIC
               MOVE IH-STMT-DATE TO FM-DATE
           ELSE
               MOVE 0 TO FM-DATE
           END-IF
           CALL 'FMTUTIL' USING FMT-PARMS
           MOVE FM-OUT TO OD-DATE
           DISPLAY OUT-DATE-LINE
           DISPLAY OUT-OPEN-LINE
           DISPLAY OUT-COLUMNS.

       2100-PAGE-HEADER.
           MOVE WS-PAGE TO OP-PAGE
           DISPLAY OUT-PAGE-HDR.

       3000-DETAIL.
           IF IT-DATE NOT NUMERIC OR IT-AMOUNT NOT NUMERIC
               MOVE IT-DATE TO OBT-DATE
               DISPLAY OUT-BAD-TXN
           ELSE
               MOVE 'W' TO DP-FUNCTION
               MOVE IT-DATE TO DP-DATE-1
               CALL 'DATEUTIL' USING DATE-PARMS
               IF DP-RETURN-CODE NOT = 0
                   MOVE IT-DATE TO OBT-DATE
                   DISPLAY OUT-BAD-TXN
               ELSE
                   PERFORM 3100-PRINT-DETAIL
               END-IF
           END-IF.

       3100-PRINT-DETAIL.
           IF WS-LINES = 10
               DISPLAY '--- CONTINUED ---'
               ADD 1 TO WS-PAGE
               PERFORM 2100-PAGE-HEADER
               DISPLAY OUT-COLUMNS
               MOVE 0 TO WS-LINES
           END-IF
           IF IT-CODE = 'W' OR IT-CODE = 'F'
               COMPUTE WS-SIGNED = 0 - IT-AMOUNT
               ADD 1 TO WS-DEBITS
           ELSE
               MOVE IT-AMOUNT TO WS-SIGNED
               ADD 1 TO WS-CREDITS
           END-IF
           ADD WS-SIGNED TO WS-BAL
           MOVE IT-DATE TO WS-DATE-X
           MOVE WS-DATE-X(5:2) TO ODT-MM
           MOVE WS-DATE-X(7:2) TO ODT-DD
           MOVE WS-DAY-NAME(DP-DOW) TO ODT-DAY
           MOVE IT-DESC TO ODT-DESC
           MOVE 'B' TO FM-STYLE
           MOVE WS-SIGNED TO FM-AMOUNT
           CALL 'FMTUTIL' USING FMT-PARMS
           MOVE FM-OUT TO ODT-AMT
           MOVE WS-BAL TO ODT-BAL
           DISPLAY OUT-DETAIL
           ADD 1 TO WS-LINES.

       4000-CLOSE-STATEMENT.
           MOVE 'R' TO FM-STYLE
           MOVE WS-BAL TO FM-AMOUNT
           CALL 'FMTUTIL' USING FMT-PARMS
           MOVE FM-OUT TO OC-BAL
           MOVE WS-DEBITS TO OC-DEBITS
           MOVE WS-CREDITS TO OC-CREDITS
           DISPLAY OUT-CLOSE-LINE
           MOVE 'N' TO WS-IN-STMT.
